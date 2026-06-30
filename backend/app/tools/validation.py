"""Pre-PR validation: catch broken code BEFORE the PR opens, not after.

`open_pull_request` already syntax-checks .py/.json. That misses a whole class
of bugs that still parse fine — undefined names (the F821 that bit us), unused
imports, bad f-strings — and of course failing tests. This module adds two
deeper, configurable gates (see Settings.prepr_validation):

  lint  -> run `ruff` over the proposed Python files, restricted to the
           pyflakes ("F") rules so we flag probable BUGS, not style nits.
           Static analysis: it never executes the generated code.
  full  -> additionally materialize the files over a fresh checkout of the
           base branch, install deps, and run pytest. This DOES execute the
           generated code, so it's opt-in.

The gate is "fail closed for real problems, open for green": if validation can't
run (tool missing, download failed), it does NOT block the PR — CI is still the
backstop. It only blocks when it positively finds a lint error or test failure.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

_API = "https://api.github.com"


@dataclass
class ValidationResult:
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    log: str = ""

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(passed=True)


def validate_files(files: list[dict[str, Any]]) -> ValidationResult:
    """Run the configured pre-PR validation level over the proposed files."""
    level = get_settings().prepr_validation
    if level == "off" or not files:
        return ValidationResult.ok()

    lint = _run_lint(files)
    if not lint.passed:
        return lint

    if level == "full":
        return _run_full(files)
    return ValidationResult.ok()


# --- lint: static, no code execution -----------------------------------
def _run_lint(files: list[dict[str, Any]]) -> ValidationResult:
    py_files = [f for f in files if str(f.get("path", "")).endswith(".py")]
    if not py_files:
        return ValidationResult.ok()

    timeout = get_settings().prepr_validation_timeout
    with tempfile.TemporaryDirectory(prefix="prepr-lint-") as tmp:
        root = Path(tmp)
        written: list[str] = []
        for f in py_files:
            dest = root / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f["content"], encoding="utf-8")
            written.append(f["path"])
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "ruff", "check",
                    "--isolated",      # ignore any ambient ruff config
                    "--select", "F",   # pyflakes: probable bugs, not style
                    "--output-format", "concise",
                    *written,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return ValidationResult.ok()  # ruff not available -> don't block
        except subprocess.TimeoutExpired:
            return ValidationResult.ok()  # can't conclude -> don't block

    if proc.returncode == 0:
        return ValidationResult.ok()
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()][:20]
    return ValidationResult(
        passed=False,
        errors=lines or ["ruff reported lint errors"],
        log=out,
    )


# --- full: overlay a real checkout, install, run pytest -----------------
def _run_full(files: list[dict[str, Any]]) -> ValidationResult:
    repo_dir = _download_base_checkout()
    if repo_dir is None:
        return ValidationResult.ok()  # couldn't fetch the repo -> don't block

    timeout = get_settings().prepr_validation_timeout
    try:
        for f in files:
            dest = repo_dir / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f["content"], encoding="utf-8")

        # Best-effort dependency install if the repo declares any.
        req = repo_dir / "requirements.txt"
        if req.is_file():
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass  # install best-effort; let pytest report what's missing

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return ValidationResult.ok()
        except subprocess.TimeoutExpired:
            return ValidationResult.ok()

        # pytest exit codes: 0 = passed, 5 = no tests collected. Neither blocks.
        if proc.returncode in (0, 5):
            return ValidationResult.ok()
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        tail = out.strip().splitlines()[-25:]
        return ValidationResult(
            passed=False,
            errors=["pytest reported failing tests before the PR could open"],
            log="\n".join(tail),
        )
    finally:
        _cleanup(repo_dir)


def _download_base_checkout() -> Path | None:
    """Fetch the base branch as a tarball and extract it. None on any failure."""
    s = get_settings()
    if not s.github_repo:
        return None
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=60.0, headers=headers, follow_redirects=True) as c:
            r = c.get(f"{_API}/repos/{s.github_repo}/tarball/{s.github_base_branch}")
            r.raise_for_status()
            data = r.content
    except httpx.HTTPError:
        return None

    tmp = Path(tempfile.mkdtemp(prefix="prepr-full-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            _safe_extract(tar, tmp)
    except (tarfile.TarError, OSError):
        _cleanup(tmp)
        return None
    # GitHub tarballs wrap everything in a single top-level dir.
    children = [p for p in tmp.iterdir() if p.is_dir()]
    return children[0] if len(children) == 1 else tmp


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract, refusing any member that would escape `dest` (path traversal)."""
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest)):
            raise tarfile.TarError(f"unsafe path in tarball: {member.name}")
    tar.extractall(dest)


def _cleanup(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)

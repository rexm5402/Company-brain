"""CODEOWNERS file parser.

Fetches the `.github/CODEOWNERS` file from the target repo and maps a
file path to the best-matching owner (GitHub username). Falls back to
`WATCHDOG_DEFAULT_ASSIGNEE` when no pattern matches.

CODEOWNERS format (GitHub standard):
  # comment
  pattern  @owner1  @owner2
  /backend/  @alice  @backend-team
  *.py  @bob

We pick the LAST matching pattern (GitHub's tie-breaking rule) and return
the first individual @username we find (skipping @team handles, which
can't be mapped to a single assignee without a Teams API call).
"""
from __future__ import annotations

import fnmatch
import logging
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_API = "https://api.github.com"

# Cache the parsed rules for the lifetime of the process. The watchdog
# re-fetches on first use or if the cache is explicitly cleared.
_rules_cache: Optional[list[tuple[str, list[str]]]] = None  # [(pattern, [owners])]


def _fetch_codeowners() -> Optional[str]:
    """Return raw CODEOWNERS text from the repo, or None on any failure."""
    s = get_settings()
    if not s.github_repo or not s.github_token:
        return None
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        try:
            with httpx.Client(timeout=10.0, headers=headers) as c:
                r = c.get(
                    f"{_API}/repos/{s.github_repo}/contents/{path}",
                    params={"ref": s.github_base_branch},
                )
                if r.status_code == 200:
                    # GitHub returns base64-encoded content by default;
                    # raw+json returns plain text.
                    return r.text
        except httpx.HTTPError:
            continue
    return None


def _parse(text: str) -> list[tuple[str, list[str]]]:
    """Return list of (pattern, [owners]) in file order (last match wins)."""
    rules: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern = parts[0]
        owners = [p.lstrip("@") for p in parts[1:] if p.startswith("@")]
        if owners:
            rules.append((pattern, owners))
    return rules


def _match(file_path: str, rules: list[tuple[str, list[str]]]) -> Optional[str]:
    """Return the first individual owner from the last matching rule, or None."""
    matched_owners: list[str] = []
    for pattern, owners in rules:
        # Normalize: CODEOWNERS patterns are repo-relative; strip leading slash.
        pat = pattern.lstrip("/")
        # Directory patterns (trailing slash) match everything inside.
        if pat.endswith("/"):
            pat = pat + "**"
        fp = file_path.lstrip("/")
        if fnmatch.fnmatch(fp, pat) or fnmatch.fnmatch(fp, f"**/{pat}"):
            matched_owners = owners  # keep updating; last match wins
    # Prefer individual usernames over team handles (teams contain a slash or
    # are multi-word; individuals are simple alphanumeric slugs).
    for owner in matched_owners:
        if "/" not in owner:  # skip org/team handles like "acme/backend-team"
            return owner
    return None


def resolve_owner(file_path: str) -> Optional[str]:
    """Return the GitHub username that owns `file_path`, or None."""
    global _rules_cache
    if _rules_cache is None:
        raw = _fetch_codeowners()
        _rules_cache = _parse(raw) if raw else []
        if _rules_cache:
            logger.info("CODEOWNERS loaded: %d rules", len(_rules_cache))
        else:
            logger.warning("CODEOWNERS not found or empty in target repo")

    owner = _match(file_path, _rules_cache)
    if owner:
        return owner
    # Fallback to configured default
    default = get_settings().watchdog_default_assignee
    return default or None


def clear_cache() -> None:
    """Force re-fetch on next call (useful after a CODEOWNERS update)."""
    global _rules_cache
    _rules_cache = None

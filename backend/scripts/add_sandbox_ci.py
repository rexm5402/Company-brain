"""One-shot: install a minimal CI workflow into the sandbox repo.

The agent opens PRs against GITHUB_REPO; this gives that repo a GitHub Actions
workflow so every PR gets a green/red check the dashboard can surface. The
workflow is dependency-free: it compiles all Python and validates all JSON, and
runs pytest only if tests are present.

Run once:  python -m scripts.add_sandbox_ci
Needs the PAT to include the "Workflows" permission (read/write).
"""
from __future__ import annotations

import base64
import sys

import httpx

from app.config import get_settings

_API = "https://api.github.com"
_PATH = ".github/workflows/ci.yml"

_WORKFLOW = """\
name: CI
on:
  pull_request:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Compile Python
        run: python -m compileall -q .
      - name: Validate JSON
        run: |
          import glob, json, sys
          bad = []
          for f in glob.glob('**/*.json', recursive=True):
              try:
                  json.load(open(f))
              except Exception as e:
                  bad.append(f'{f}: {e}')
          if bad:
              print('\\n'.join(bad)); sys.exit(1)
          print('json ok')
        shell: python {0}
      - name: Run tests if present
        run: |
          if ls tests/*.py test_*.py *_test.py >/dev/null 2>&1; then
            pip install pytest && pytest -q
          else
            echo "no tests present"
          fi
"""


def main() -> int:
    s = get_settings()
    if not s.github_repo:
        print("GITHUB_REPO not configured", file=sys.stderr)
        return 1
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{_API}/repos/{s.github_repo}/contents/{_PATH}"
    with httpx.Client(timeout=30.0, headers=headers) as client:
        existing = client.get(url, params={"ref": s.github_base_branch})
        payload = {
            "message": "Add CI workflow (compile + json + tests)",
            "content": base64.b64encode(_WORKFLOW.encode()).decode(),
            "branch": s.github_base_branch,
        }
        if existing.status_code == 200:
            payload["sha"] = existing.json()["sha"]
            print(f"{_PATH} exists; updating.")
        r = client.put(url, json=payload)
        if r.status_code >= 400:
            print(f"FAILED {r.status_code}: {r.text[:400]}", file=sys.stderr)
            if r.status_code == 403:
                print(
                    "\nThe PAT likely lacks the 'Workflows' permission. Add it "
                    "(read/write) to the fine-grained token and re-run.",
                    file=sys.stderr,
                )
            return 1
    print(f"OK: committed {_PATH} to {s.github_repo}@{s.github_base_branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

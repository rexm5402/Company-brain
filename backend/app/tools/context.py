"""Per-run shared state for tools.

The read set is the heart of the read-before-write guarantee: every file the
agent reads via `get_file_contents` is recorded here with the exact blob SHA it
was shown. `open_pull_request` consults this set and refuses to overwrite an
existing file the agent never read (or read at a now-stale SHA).

A fresh RunContext is created per agent run, so nothing leaks across runs (which
would reintroduce staleness).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunContext:
    # path -> blob sha that was handed to the agent during this run
    read_files: dict[str, str] = field(default_factory=dict)

    def record_read(self, path: str, sha: str) -> None:
        self.read_files[path] = sha

    def read_sha(self, path: str) -> str | None:
        return self.read_files.get(path)

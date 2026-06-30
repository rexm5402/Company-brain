"""Tests for RunTestsTool.

We mock _run_full so tests stay fast (no real subprocess/network calls).
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("GITHUB_TOKEN", "test")
os.environ.setdefault("GITHUB_REPO", "test/repo")

from app.tools.test_tool import RunTestsTool
from app.tools.validation import ValidationResult


_GOOD_FILES = [{"path": "app/foo.py", "content": "def add(a, b):\n    return a + b\n"}]
_FAIL_LOG = "FAILED tests/test_foo.py::test_add - AssertionError"


def _ok():
    return ValidationResult(passed=True)


def _fail():
    return ValidationResult(passed=False, errors=["pytest reported failing tests"], log=_FAIL_LOG)


def test_passes_when_tests_pass():
    tool = RunTestsTool()
    with patch("app.tools.validation._run_full", return_value=_ok()):
        result = tool.run(files=_GOOD_FILES)
    assert result.success
    assert result.output is not None
    assert result.output["status"] == "all tests passed"


def test_fails_when_tests_fail():
    tool = RunTestsTool()
    with patch("app.tools.validation._run_full", return_value=_fail()):
        result = tool.run(files=_GOOD_FILES)
    assert not result.success
    assert result.output is not None
    assert _FAIL_LOG in result.output.get("test_output", "")


def test_empty_files_returns_error():
    tool = RunTestsTool()
    result = tool.run(files=[])
    assert not result.success
    assert result.error is not None


def test_long_log_is_truncated():
    long_log = "x" * 10000
    fail = ValidationResult(passed=False, errors=["failing"], log=long_log)
    tool = RunTestsTool()
    with patch("app.tools.validation._run_full", return_value=fail):
        result = tool.run(files=_GOOD_FILES)
    assert not result.success
    assert len(result.output["test_output"]) <= 4200  # MAX_LOG_CHARS + header

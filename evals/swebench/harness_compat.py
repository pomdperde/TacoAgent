from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path
from typing import Any


_ORIGINAL_WRITE_TEXT = Path.write_text


def write_text_with_posix_shell_newlines(
    path: Path,
    data: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> int:
    """Prevent Windows from corrupting shell scripts copied into Linux containers."""
    if path.suffix.lower() != ".sh":
        kwargs: dict[str, Any] = {"encoding": encoding, "errors": errors}
        if newline is not None:
            kwargs["newline"] = newline
        return _ORIGINAL_WRITE_TEXT(path, data, **kwargs)
    with path.open(
        mode="w",
        encoding=encoding or "utf-8",
        errors=errors,
        newline="\n",
    ) as handle:
        return handle.write(data.replace("\r\n", "\n").replace("\r", "\n"))


def matching_parameterized_family_statuses(case: str, status_map: dict[str, str]) -> list[str]:
    """Find statuses for pytest's omitted empty parameter ID in old harness logs."""
    if not case.endswith("[]") or case in status_map:
        return []
    prefix = case[:-1]
    return [status for name, status in status_map.items() if name.startswith(prefix)]


def install_grading_compatibility() -> None:
    """Patch SWE-bench 4.1.0's exact-only pytest parameter matching."""
    if sys.platform == "win32" and "resource" not in sys.modules:
        sys.modules["resource"] = types.ModuleType("resource")
    from swebench.harness import grading
    from swebench.harness.constants import TestStatus

    passing = {TestStatus.PASSED.value, TestStatus.XFAIL.value}
    failing = {TestStatus.FAILED.value, TestStatus.ERROR.value}

    def test_passed(case: str, status_map: dict[str, str]) -> bool:
        if case in status_map:
            return status_map[case] in passing
        family = matching_parameterized_family_statuses(case, status_map)
        return bool(family) and all(status in passing for status in family)

    def test_failed(case: str, status_map: dict[str, str]) -> bool:
        if case in status_map:
            return status_map[case] in failing
        family = matching_parameterized_family_statuses(case, status_map)
        return not family or any(status in failing for status in family)

    grading.test_passed = test_passed
    grading.test_failed = test_failed


def main() -> None:
    if sys.platform == "win32" and "resource" not in sys.modules:
        # SWE-bench imports the Unix-only module unconditionally, but only calls
        # setrlimit on non-Windows platforms.
        sys.modules["resource"] = types.ModuleType("resource")
    Path.write_text = write_text_with_posix_shell_newlines
    install_grading_compatibility()
    runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")


if __name__ == "__main__":
    main()

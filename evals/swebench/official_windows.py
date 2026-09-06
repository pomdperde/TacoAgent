from __future__ import annotations

import runpy
from pathlib import Path


def _force_lf_path_writes() -> None:
    """Keep shell scripts and patches consumable by Linux Docker on Windows."""
    original = Path.write_text

    def write_text_lf(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        selected_newline = "\n" if self.suffix.lower() in {".sh", ".diff", ".patch"} else newline
        return original(
            self,
            data,
            encoding=encoding,
            errors=errors,
            newline=selected_newline,
        )

    Path.write_text = write_text_lf  # type: ignore[method-assign]


def main() -> None:
    _force_lf_path_writes()
    runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")


if __name__ == "__main__":
    main()

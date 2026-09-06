from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tacoagent.graph.patch_status import inspect_workspace_patch


class PatchStatusTests(unittest.TestCase):
    def test_distinguishes_tests_from_production_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "tests").mkdir()
            (root / "tests" / "test_fix.py").write_text("test = True\n", encoding="utf-8")
            status = inspect_workspace_patch(root)
            self.assertTrue(status["has_patch"])
            self.assertFalse(status["production_files_changed"])
            (root / "package.py").write_text("fixed = True\n", encoding="utf-8")
            status = inspect_workspace_patch(root)
            self.assertTrue(status["production_files_changed"])
            self.assertEqual(status["production_files"], ["package.py"])

    def test_excludes_temporary_fix_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "_fix_example.py").write_text("print('temporary')\n", encoding="utf-8")
            status = inspect_workspace_patch(root)
            self.assertTrue(status["has_patch"])
            self.assertFalse(status["production_files_changed"])

    def test_reports_auxiliary_only_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "_patch_module.py").write_text("print('temporary')\n", encoding="utf-8")
            status = inspect_workspace_patch(root)
            self.assertTrue(status["auxiliary_only"])
            self.assertEqual(status["auxiliary_files"], ["_patch_module.py"])
            self.assertFalse(status["production_files_changed"])


if __name__ == "__main__":
    unittest.main()

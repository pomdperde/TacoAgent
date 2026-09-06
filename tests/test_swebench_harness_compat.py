import tempfile
import unittest
from pathlib import Path

from evals.swebench.harness_compat import (
    install_grading_compatibility,
    matching_parameterized_family_statuses,
    write_text_with_posix_shell_newlines,
)


class HarnessCompatTests(unittest.TestCase):
    def test_shell_script_is_written_with_lf_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eval.sh"
            write_text_with_posix_shell_newlines(path, "#!/bin/bash\r\necho ok\r\n")
            content = path.read_bytes()
        self.assertEqual(content, b"#!/bin/bash\necho ok\n")

    def test_non_shell_text_uses_normal_path_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.txt"
            write_text_with_posix_shell_newlines(path, "ok\n", encoding="utf-8")
            content = path.read_text(encoding="utf-8")
        self.assertEqual(content, "ok\n")

    def test_empty_parameter_id_matches_only_its_parameterized_family(self) -> None:
        statuses = {
            "pkg/test_mod.py::test_roundtrip[A]": "PASSED",
            "pkg/test_mod.py::test_roundtrip[B]": "PASSED",
            "pkg/test_mod.py::test_other[A]": "FAILED",
        }
        self.assertEqual(
            matching_parameterized_family_statuses(
                "pkg/test_mod.py::test_roundtrip[]", statuses
            ),
            ["PASSED", "PASSED"],
        )
        self.assertEqual(
            matching_parameterized_family_statuses(
                "pkg/test_mod.py::missing[]", statuses
            ),
            [],
        )

    def test_grading_compatibility_requires_every_family_member_to_pass(self) -> None:
        install_grading_compatibility()
        from swebench.harness import grading

        case = "pkg/test_mod.py::test_roundtrip[]"
        self.assertTrue(grading.test_passed(case, {
            "pkg/test_mod.py::test_roundtrip[A]": "PASSED",
            "pkg/test_mod.py::test_roundtrip[B]": "XFAIL",
        }))
        self.assertFalse(grading.test_failed(case, {
            "pkg/test_mod.py::test_roundtrip[A]": "PASSED",
            "pkg/test_mod.py::test_roundtrip[B]": "XFAIL",
        }))
        self.assertFalse(grading.test_passed(case, {
            "pkg/test_mod.py::test_roundtrip[A]": "PASSED",
            "pkg/test_mod.py::test_roundtrip[B]": "FAILED",
        }))
        self.assertTrue(grading.test_failed(case, {
            "pkg/test_mod.py::test_roundtrip[A]": "PASSED",
            "pkg/test_mod.py::test_roundtrip[B]": "FAILED",
        }))


if __name__ == "__main__":
    unittest.main()

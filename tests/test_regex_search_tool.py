import json
import tempfile
import unittest
from pathlib import Path

from tacoagent import TacoAgent
from tacoagent.core.workspace import WorkspaceBoundaryError, WorkspaceSandbox
from tacoagent.tools import RegexSearchTool


class RegexSearchToolTests(unittest.TestCase):
    def test_searches_files_with_line_and_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.txt").write_text("alpha 123\nbeta 456", encoding="utf-8")
            result = RegexSearchTool(WorkspaceSandbox(root)).invoke(
                pattern=r"\d+", path="one.txt"
            )
            self.assertEqual(result["match_count"], 2)
            self.assertEqual(result["matches"][0]["line"], 1)
            self.assertEqual(result["matches"][0]["column"], 7)

    def test_supports_case_insensitive_directory_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.txt").write_text("Taco", encoding="utf-8")
            (root / "two.txt").write_text("taco", encoding="utf-8")
            result = RegexSearchTool(WorkspaceSandbox(root)).invoke(
                pattern="taco", case_sensitive=False
            )
            self.assertEqual(result["match_count"], 2)

    def test_rejects_invalid_regex_and_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool = RegexSearchTool(WorkspaceSandbox(directory))
            with self.assertRaisesRegex(ValueError, "Invalid regular expression"):
                tool.invoke(pattern="[")
            with self.assertRaises(WorkspaceBoundaryError):
                tool.invoke(pattern="secret", path="../")

    def test_agent_search_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.py").write_text("TODO: taco", encoding="utf-8")
            response = TacoAgent(root).run(r"/grep TODO code.py")
            self.assertIn("code.py:1:1", response.content)
            records = [
                json.loads(line)
                for line in (root / ".tacoagent/tool_calls.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(records[0]["tool_name"], "regex_search")
            self.assertEqual(records[-1]["event"], "tool_succeeded")


if __name__ == "__main__":
    unittest.main()

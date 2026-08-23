import unittest
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tacoagent.tools import ToolRegistry, build_tool_registry


@dataclass
class EchoTool:
    name: str = "echo"
    description: str = "Return the supplied text."

    def invoke(self, **kwargs):
        return kwargs["text"]


@dataclass
class FailingTool:
    name: str = "failing"
    description: str = "Always fail."

    def invoke(self, **kwargs):
        raise RuntimeError("boom")


class ToolRegistryTests(unittest.TestCase):
    def test_register_and_invoke(self) -> None:
        registry = build_tool_registry([EchoTool()])
        self.assertEqual(registry.names(), ["echo"])
        self.assertEqual(registry.invoke("echo", text="taco"), "taco")

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = ToolRegistry([EchoTool()])
        with self.assertRaises(ValueError):
            registry.register(EchoTool())

    def test_success_and_failure_are_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tools.jsonl")
            registry = build_tool_registry([EchoTool(), FailingTool()], log_path=path)
            registry.invoke("echo", task_name="say taco", text="taco")
            with self.assertRaises(RuntimeError):
                registry.invoke("failing", task_name="break intentionally")
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [record["event"] for record in records],
                ["tool_started", "tool_succeeded", "tool_started", "tool_failed"],
            )
            self.assertEqual(records[1]["result"], "taco")
            self.assertEqual(records[3]["task_name"], "break intentionally")


if __name__ == "__main__":
    unittest.main()

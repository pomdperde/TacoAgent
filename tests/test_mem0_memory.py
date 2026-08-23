import tempfile
import unittest
from unittest.mock import patch

from tacoagent.memory import (
    MAX_COMPRESSED_MEMORY,
    add_mem0_task_memory,
    compress_task_memory,
    search_mem0_task_memories,
)
from tacoagent.memory import generate_task_summary, save_task_summary


class FakeMem0:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []

    def add(self, text, **kwargs):
        self.add_calls.append((text, kwargs))
        return {"results": [{"id": "vector-1"}]}

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return {
            "results": [
                {
                    "id": "vector-1",
                    "memory": "compressed result",
                    "score": 0.91,
                    "metadata": {
                        "memory_id": "memory-1",
                        "task": "old task",
                        "summary": "old result",
                        "status": "completed",
                    },
                }
            ]
        }


class Mem0MemoryTests(unittest.TestCase):
    def test_compression_removes_completed_todos_and_duplicate_values(self) -> None:
        compact = compress_task_memory(
            {
                "task": "  build   app ",
                "summary": "app completed",
                "plan_summary": "app completed",
                "todos": [
                    {"content": "finished", "status": "completed"},
                    {"content": "deploy", "status": "pending"},
                ],
                "tools_used": ["pytest", "pytest"],
                "important_files": ["app.py", "app.py"],
            }
        )
        self.assertNotIn("finished", compact["text"])
        self.assertIn("deploy", compact["text"])
        self.assertEqual(compact["text"].count("pytest"), 1)
        self.assertEqual(compact["text"].count("app.py"), 1)
        self.assertLessEqual(len(compact["text"]), MAX_COMPRESSED_MEMORY)

    def test_add_uses_infer_false_and_local_mem0(self) -> None:
        fake = FakeMem0()
        summary = generate_task_summary(
            "build app", [], final_answer="completed", session_id="session-1"
        )
        with patch("tacoagent.memory.memory._create_mem0", return_value=fake):
            result = add_mem0_task_memory(".", summary)

        self.assertTrue(result["stored"])
        _, kwargs = fake.add_calls[0]
        self.assertFalse(kwargs["infer"])
        self.assertEqual(kwargs["user_id"], "session-1")
        self.assertEqual(kwargs["agent_id"], "tacoagent")

    def test_duplicate_fingerprint_skips_vector_write(self) -> None:
        fake = FakeMem0()
        summary = generate_task_summary("same", [], final_answer="same result")
        fingerprint = compress_task_memory(summary)["fingerprint"]
        with patch("tacoagent.memory.memory._create_mem0", return_value=fake):
            result = add_mem0_task_memory(
                ".", summary, known_fingerprints={fingerprint}
            )
        self.assertTrue(result["deduplicated"])
        self.assertFalse(fake.add_calls)

    def test_search_maps_mem0_results_for_layered_memory(self) -> None:
        fake = FakeMem0()
        with patch("tacoagent.memory.memory._create_mem0", return_value=fake):
            results = search_mem0_task_memories(
                ".", "database", session_id="session-1", limit=3
            )
        self.assertEqual(results[0]["summary"], "old result")
        self.assertEqual(results[0]["storage"], "mem0-local-qdrant")

    def test_save_audit_marks_second_identical_memory_as_deduplicated(self) -> None:
        fake = FakeMem0()
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.memory.memory._create_mem0", return_value=fake
        ):
            first = generate_task_summary("same", [], final_answer="same result")
            second = generate_task_summary("same", [], final_answer="same result")
            saved_first = save_task_summary(directory, first)
            saved_second = save_task_summary(directory, second)

        self.assertTrue(saved_first["mem0_storage"]["stored"])
        self.assertTrue(saved_second["mem0_storage"]["deduplicated"])
        self.assertEqual(len(fake.add_calls), 1)


if __name__ == "__main__":
    unittest.main()

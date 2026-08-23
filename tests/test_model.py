import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tacoagent.core import model as model_module


class ModelFactoryTests(unittest.TestCase):
    def test_loads_key_and_model_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory, ".env")
            env_file.write_text(
                "OPENAI_API_KEY=test-key\nOPENAI_MODEL=test-model\n"
                "OPENAI_BASE_URL=https://example.test/v1\n",
                encoding="utf-8",
            )
            captured = {}

            def fake_chat_openai(**kwargs):
                captured.update(kwargs)
                return kwargs

            with patch.dict(os.environ, {}, clear=True), patch.object(
                model_module, "ChatOpenAI", side_effect=fake_chat_openai
            ):
                result = model_module.create_model(dotenv_path=env_file, temperature=0)
            self.assertEqual(result["api_key"], "test-key")
            self.assertEqual(result["model"], "test-model")
            self.assertEqual(result["base_url"], "https://example.test/v1")
            self.assertEqual(result["temperature"], 0)

    def test_missing_key_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {}, clear=True
        ):
            env_file = Path(directory, ".env")
            env_file.write_text("OPENAI_MODEL=test-model\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing OPENAI_API_KEY"):
                model_module.create_model(dotenv_path=env_file)


if __name__ == "__main__":
    unittest.main()

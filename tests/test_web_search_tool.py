from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tacoagent.tools.web_search_tool import WebSearchTool


class FakeTavilyClient:
    def search(self, **kwargs):
        self.kwargs = kwargs
        return {
            "answer": "TacoAgent is a test subject.",
            "results": [
                {
                    "title": "Official docs",
                    "url": "https://example.com/docs",
                    "content": "Reliable details",
                    "score": 0.95,
                }
            ],
        }


class WebSearchToolTests(unittest.TestCase):
    def test_missing_api_key_returns_structured_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = WebSearchTool().invoke({"query": "test query"})

        self.assertEqual(result, {"ok": False, "error": "missing TAVILY_API_KEY"})

    def test_normalizes_tavily_results(self) -> None:
        client = FakeTavilyClient()
        tool = WebSearchTool(api_key="test-key", client=client)

        result = tool.invoke({"query": "TacoAgent", "max_results": 3})

        self.assertTrue(result["ok"])
        self.assertEqual(result["query"], "TacoAgent")
        self.assertEqual(result["results"][0]["score"], 0.95)
        self.assertEqual(client.kwargs["max_results"], 3)


if __name__ == "__main__":
    unittest.main()

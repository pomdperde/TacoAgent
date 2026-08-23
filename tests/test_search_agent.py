from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from tacoagent.agents.search_agent import run_search_agent


class FakeModel:
    def __init__(self):
        self.responses = iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "search-1",
                            "name": "WebSearchTool",
                            "args": {"query": "official TacoAgent facts"},
                        }
                    ],
                ),
                AIMessage(content="Research complete with one official source."),
            ]
        )

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, _messages):
        return next(self.responses)


class FakeSearchTool:
    name = "WebSearchTool"

    def invoke(self, args):
        return {
            "ok": True,
            "query": args["query"],
            "answer": "A researched answer",
            "results": [
                {
                    "title": "Official",
                    "url": "https://example.com/official",
                    "content": "facts",
                    "score": 1.0,
                }
            ],
        }


class SearchAgentTests(unittest.TestCase):
    def test_runs_search_react_loop_and_collects_sources(self) -> None:
        written = []
        with (
            patch("tacoagent.agents.search_agent.create_model", return_value=FakeModel()),
            patch("tacoagent.agents.search_agent.WebSearchTool", return_value=FakeSearchTool()),
        ):
            result = run_search_agent(
                {"task": "research", "research_notes": "prior note"},
                "find reliable facts",
                writer=written.append,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["queries"], ["official TacoAgent facts"])
        self.assertEqual(result["sources"][0]["url"], "https://example.com/official")
        self.assertEqual(result["summary"], "Research complete with one official source.")
        self.assertEqual([event["type"] for event in written], ["tool_call", "search_results"])
        self.assertTrue(any(isinstance(message, ToolMessage) for message in result["messages"]))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from typing import Annotated, get_args, get_origin, get_type_hints

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from tacoagent.graph.state import TacoGraphState


class TacoGraphStateTests(unittest.TestCase):
    def test_messages_field_uses_add_messages_reducer(self) -> None:
        annotation = get_type_hints(TacoGraphState, include_extras=True)["messages"]

        self.assertIs(get_origin(annotation), Annotated)
        self.assertIn(add_messages, get_args(annotation)[1:])

    def test_graph_appends_messages_instead_of_overwriting(self) -> None:
        graph = StateGraph(TacoGraphState)
        graph.add_node("reply", lambda _: {"messages": [AIMessage(content="done")]})
        graph.add_edge(START, "reply")
        graph.add_edge("reply", END)

        result = graph.compile().invoke(
            {"messages": [HumanMessage(content="task")]}
        )

        self.assertEqual([message.content for message in result["messages"]], ["task", "done"])


if __name__ == "__main__":
    unittest.main()

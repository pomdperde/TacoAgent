from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class WebSearchInput(BaseModel):
    query: str = Field(description="Focused factual web-search query")
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchTool(BaseTool):
    """Search the web with Tavily and normalize the provider response."""

    name: str = "WebSearchTool"
    description: str = (
        "Search the web for reliable factual information and return an answer "
        "with ranked source results."
    )
    args_schema: type[BaseModel] = WebSearchInput
    api_key: str | None = Field(default=None, exclude=True)
    client: Any = Field(default=None, exclude=True)

    def _run(self, query: str, max_results: int = 5) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {"ok": False, "error": "query must not be empty"}
        api_key = (self.api_key or os.getenv("TAVILY_API_KEY", "")).strip()
        if not api_key:
            return {"ok": False, "error": "missing TAVILY_API_KEY"}

        try:
            client = self.client
            if client is None:
                from tavily import TavilyClient

                client = TavilyClient(api_key=api_key)
            payload = client.search(
                query=query,
                search_depth="advanced",
                include_answer=True,
                max_results=max_results,
            )
            results = []
            for item in payload.get("results", []):
                results.append(
                    {
                        "title": str(item.get("title", "")),
                        "url": str(item.get("url", "")),
                        "content": str(item.get("content", "")),
                        "score": item.get("score"),
                    }
                )
            return {
                "ok": True,
                "query": query,
                "answer": str(payload.get("answer", "")),
                "results": results,
            }
        except Exception as exc:
            return {
                "ok": False,
                "query": query,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def _arun(self, query: str, max_results: int = 5) -> dict[str, Any]:
        return self._run(query=query, max_results=max_results)

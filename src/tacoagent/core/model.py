from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv
from langchain_openai import ChatOpenAI


DEFAULT_MODEL = "gpt-4o-mini"


def _find_configured_dotenv(start: Path) -> str:
    """Find the nearest .env whose OPENAI_API_KEY is not blank."""
    for directory in (start.resolve(), *start.resolve().parents):
        candidate = directory / ".env"
        if not candidate.is_file():
            continue
        values = dotenv_values(candidate)
        if str(values.get("OPENAI_API_KEY") or "").strip():
            return str(candidate)
    return ""


def create_model(
    model: str | None = None,
    *,
    dotenv_path: str | Path | None = None,
    **kwargs: Any,
) -> ChatOpenAI:
    """Create ``ChatOpenAI`` using ``OPENAI_API_KEY`` loaded from ``.env``."""
    env_file = (
        str(Path(dotenv_path).expanduser().resolve())
        if dotenv_path is not None
        else _find_configured_dotenv(Path.cwd())
    )
    if env_file:
        load_dotenv(env_file, override=False)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        location = env_file or str(Path.cwd() / ".env")
        raise ValueError(
            f"Missing OPENAI_API_KEY. Add it to {location} or the process environment."
        )

    model_name = (model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip()
    base_url = (
        os.getenv("OPENAI_BASE_URL", "").strip()
        or os.getenv("OPENAI_API_BASE", "").strip()
    )
    if base_url and "base_url" not in kwargs and "openai_api_base" not in kwargs:
        kwargs["base_url"] = base_url
    return ChatOpenAI(model=model_name, api_key=api_key, **kwargs)

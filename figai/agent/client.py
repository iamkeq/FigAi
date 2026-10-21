"""OpenRouter client: the OpenAI SDK pointed at OpenRouter's base URL."""

from __future__ import annotations

from openai import OpenAI

from figai.config import Config

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def build_client(config: Config) -> OpenAI:
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=config.settings.openrouter_api_key)

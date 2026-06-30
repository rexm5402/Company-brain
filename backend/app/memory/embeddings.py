"""Embedding helper — wraps the OpenAI embeddings API."""
from __future__ import annotations

import openai

from app.config import get_settings


def embed(text: str) -> list[float]:
    client = openai.OpenAI(api_key=get_settings().openai_api_key)
    resp = client.embeddings.create(input=text, model="text-embedding-3-small")
    return resp.data[0].embedding

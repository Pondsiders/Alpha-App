"""inference_client.py — shared AsyncOpenAI client for local/remote inference.

Every chat and embedding call in Alpha-App goes through this module. The
underlying client reads `OPENAI_BASE_URL` and `OPENAI_API_KEY` from the
environment (see `.env`) and speaks the OpenAI REST API. Any compatible
endpoint works: Ollama's `/v1`, llmster (LM Studio), Harbormaster, a
llama-server on Modal, OpenAI itself.

Deployment knob: one line in `.env` points Alpha at whichever backend is
serving today. No code changes.
"""

from functools import lru_cache

from openai import AsyncOpenAI


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """Return the shared AsyncOpenAI client.

    The underlying HTTP connection pool is reused across calls. Environment
    variables (`OPENAI_BASE_URL`, `OPENAI_API_KEY`) are read on first access.
    """
    return AsyncOpenAI()

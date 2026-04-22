"""Embeddings client for Cortex, via OpenAI-compatible API.

Uses qwen3-embedding:4b (2560d) through whatever OpenAI-compatible endpoint
is configured via OPENAI_BASE_URL (Ollama, llmster, Harbormaster, etc.).

Qwen 3 Embedding task-aware prefixes:
- Documents: plain text (no prefix)
- Queries: "Instruct: ... \\nQuery: {text}" prefix
"""

import logfire
from openai import APIConnectionError, APIError, APITimeoutError

from alpha_app.constants import EMBED_MODEL
from alpha_app.inference_client import get_client


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass


QUERY_INSTRUCTION = (
    "Instruct: Given a memory search query, retrieve the most relevant "
    "memory entries that match the query\nQuery: "
)


async def embed_document(content: str) -> list[float]:
    """Generate embedding for a document (for storage).

    Qwen 3 Embedding: documents use plain text, no prefix.
    """
    return await _embed(content, operation="document", text=content)


async def embed_query(query: str) -> list[float]:
    """Generate embedding for a query (for search).

    Qwen 3 Embedding: queries use an instruction prefix.
    """
    return await _embed(f"{QUERY_INSTRUCTION}{query}", operation="query", text=query)


async def embed_queries_batch(queries: list[str]) -> list[list[float]]:
    """Batch-embed multiple queries in a single round-trip.

    One HTTP call instead of N. Returns embeddings in input order.
    """
    if not queries:
        return []

    prefixed = [f"{QUERY_INSTRUCTION}{q}" for q in queries]

    with logfire.span(
        "embed.batch_queries",
        model=EMBED_MODEL,
        count=len(queries),
    ):
        try:
            response = await get_client().embeddings.create(
                model=EMBED_MODEL,
                input=prefixed,
                timeout=10.0,
            )
            embeddings = [item.embedding for item in response.data]
            if len(embeddings) != len(queries):
                raise EmbeddingError(
                    f"Expected {len(queries)} embeddings, got {len(embeddings)}"
                )
            return embeddings
        except APITimeoutError:
            raise EmbeddingError("Batch embedding timed out")
        except APIConnectionError:
            raise EmbeddingError("Embedding service unreachable")
        except APIError as e:
            raise EmbeddingError(f"Batch embedding error: {e}")
        except EmbeddingError:
            raise
        except Exception as e:
            raise EmbeddingError(f"Batch embedding failed: {e}")


async def _embed(
    prompt: str,
    *,
    operation: str = "embed",
    text: str = "",
) -> list[float]:
    """Generate a single embedding via the OpenAI-compatible endpoint."""
    with logfire.span(
        "embed.{operation}",
        operation=operation,
        model=EMBED_MODEL,
        text=text,
    ):
        try:
            response = await get_client().embeddings.create(
                model=EMBED_MODEL,
                input=prompt,
                timeout=5.0,
            )
            return response.data[0].embedding
        except APITimeoutError:
            raise EmbeddingError("Embedding service timed out")
        except APIConnectionError:
            raise EmbeddingError("Embedding service unreachable")
        except APIError as e:
            raise EmbeddingError(f"Embedding service error: {e}")
        except Exception as e:
            raise EmbeddingError(f"Embedding failed: {e}")


async def health_check() -> bool:
    """Check if the inference endpoint is reachable."""
    try:
        await get_client().models.list()
        return True
    except Exception:
        return False

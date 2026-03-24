"""Ollama embeddings client for Cortex.

Uses qwen3-embedding:4b (2560d) with task-aware prefixes:
- Documents: plain text (no prefix)
- Queries: "Instruct: ... \\nQuery: {text}" prefix

Ported from alpha_sdk v0.x. Migrated from nomic-embed-text March 2026.
"""

import httpx
import logfire

from alpha_app.constants import OLLAMA_EMBED_MODEL, OLLAMA_URL


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
    """Batch-embed multiple queries in a single Ollama call.

    Uses the /api/embed endpoint (not /api/embeddings) which accepts
    an array of inputs and returns an array of embeddings. One HTTP
    round-trip instead of N.

    Returns embeddings in the same order as the input queries.
    """
    if not queries:
        return []
    if not OLLAMA_URL:
        raise EmbeddingError("OLLAMA_URL not set")

    # Add Qwen instruction prefix to each query
    prefixed = [f"{QUERY_INSTRUCTION}{q}" for q in queries]

    with logfire.span(
        "embed.batch_queries",
        model=OLLAMA_EMBED_MODEL,
        count=len(queries),
    ):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL.rstrip('/')}/api/embed",
                    json={
                        "model": OLLAMA_EMBED_MODEL,
                        "input": prefixed,
                        "keep_alive": -1,
                    },
                )
                response.raise_for_status()
                data = response.json()
                embeddings = data.get("embeddings", [])
                if len(embeddings) != len(queries):
                    raise EmbeddingError(
                        f"Expected {len(queries)} embeddings, got {len(embeddings)}"
                    )
                return embeddings
        except httpx.TimeoutException:
            raise EmbeddingError("Batch embedding timed out")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else "no response body"
            raise EmbeddingError(f"Batch embedding error {e.response.status_code}: {body}")
        except httpx.ConnectError:
            raise EmbeddingError("Embedding service unreachable")
        except EmbeddingError:
            raise
        except Exception as e:
            raise EmbeddingError(f"Batch embedding failed: {e}")


async def _embed(
    prompt: str,
    timeout: float = 5.0,
    *,
    operation: str = "embed",
    text: str = "",
) -> list[float]:
    """Call Ollama API to generate embedding."""
    if not OLLAMA_URL:
        raise EmbeddingError(
            "OLLAMA_URL not set — embeddings require a running Ollama instance"
        )
    with logfire.span(
        "embed.{operation}",
        operation=operation,
        model=OLLAMA_EMBED_MODEL,
        text=text,
    ):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{OLLAMA_URL.rstrip('/')}/api/embeddings",
                    json={
                        "model": OLLAMA_EMBED_MODEL,
                        "prompt": prompt,
                        "keep_alive": -1,  # Keep model loaded indefinitely
                    },
                )
                response.raise_for_status()
                data = response.json()
                embedding = data["embedding"]
                return embedding
        except httpx.TimeoutException:
            raise EmbeddingError("Embedding service timed out")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else "no response body"
            raise EmbeddingError(f"Embedding service error {e.response.status_code}: {body}")
        except httpx.ConnectError:
            raise EmbeddingError("Embedding service unreachable")
        except Exception as e:
            raise EmbeddingError(f"Embedding failed: {e}")


async def health_check() -> bool:
    """Check if Ollama is reachable."""
    if not OLLAMA_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{OLLAMA_URL.rstrip('/')}/api/tags")
            return response.status_code == 200
    except Exception:
        return False

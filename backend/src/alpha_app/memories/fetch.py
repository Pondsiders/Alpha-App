"""Fetch — universal URL reader with associative memory.

Fetch a URL, convert to readable text, optionally run through the
associative reading pipeline. Content delivery first, memories second.

Pipeline:
  1. URL → smart rewrite (GitHub → raw, etc.)
  2. HTTP GET with markdown preference
  3. Content-type routing:
     - text/markdown → passthrough
     - text/html → html2text → markdown
     - application/json → pretty-print
     - image/* → save to disk, return path
     - application/pdf → save to disk, return path
     - everything else → save to disk, return path
  4. (optional) Markdown → associative_read() → memories

Fetch without memories is still fetch.
"""

from __future__ import annotations

import base64
import hashlib
import json as json_mod
import re
from pathlib import Path

import httpx
import logfire

from .reading import associative_read


# -- GitHub URL rewriting -----------------------------------------------------

_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)
_GITHUB_BLOB_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$"
)
_GITHUB_TREE_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+?)/?$"
)


async def _rewrite_github_url(url: str) -> tuple[str, str | None]:
    """Rewrite GitHub URLs to fetch raw content.

    Returns (rewritten_url, description or None).
    """
    # blob → raw file
    m = _GITHUB_BLOB_RE.match(url)
    if m:
        user, repo, branch, path = m.groups()
        return (
            f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}",
            f"GitHub blob → raw ({user}/{repo}/{path})",
        )

    # tree → README in directory
    m = _GITHUB_TREE_RE.match(url)
    if m:
        user, repo, branch, path = m.groups()
        return (
            f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}/README.md",
            f"GitHub tree → README.md in {path}",
        )

    # repo root → README (need API for default branch)
    m = _GITHUB_REPO_RE.match(url)
    if m:
        user, repo = m.groups()
        default_branch = "main"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{user}/{repo}",
                    headers={
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": "Alpha/1.0",
                    },
                )
                resp.raise_for_status()
                default_branch = resp.json().get("default_branch", "main")
        except Exception:
            pass
        return (
            f"https://raw.githubusercontent.com/{user}/{repo}/{default_branch}/README.md",
            f"GitHub repo → README.md ({user}/{repo}, branch: {default_branch})",
        )

    return url, None


# -- HTTP fetch ---------------------------------------------------------------

async def _http_fetch(url: str) -> tuple[str, bytes, dict[str, str]]:
    """GET with markdown preference. Returns (content_type, body, headers)."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(
            url,
            headers={
                "Accept": "text/markdown, text/html;q=0.9, */*;q=0.5",
                "User-Agent": "Alpha/1.0 (https://alphafornow.com)",
            },
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        return content_type, resp.content, dict(resp.headers)


# -- Content conversion -------------------------------------------------------

def _html_to_markdown(html_bytes: bytes) -> str:
    """Convert HTML to markdown via html2text."""
    import html2text

    h = html2text.HTML2Text()
    h.body_width = 0  # No line wrapping
    h.ignore_links = False
    h.ignore_images = False
    h.ignore_emphasis = False

    return h.handle(html_bytes.decode("utf-8", errors="replace"))


def _save_binary(body: bytes, url: str, ext: str) -> str:
    """Save fetched binary to disk. Returns path."""
    download_dir = Path("/Pondside/Alpha-Home/downloads")
    download_dir.mkdir(parents=True, exist_ok=True)

    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    url_tail = url.rstrip("/").split("/")[-1].split("?")[0]
    if url_tail and len(url_tail) < 60:
        safe = "".join(c for c in url_tail if c.isalnum() or c in ".-_")
        if not safe.endswith(ext):
            safe = f"{safe}{ext}"
        filename = f"{url_hash}_{safe}"
    else:
        filename = f"{url_hash}{ext}"

    path = download_dir / filename
    path.write_bytes(body)
    return str(path)


_EXT_MAP = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
}


# -- Main fetch function ------------------------------------------------------

async def fetch_url(
    url: str,
    *,
    associate: bool = True,
) -> str:
    """Fetch a URL and return its content as readable text.

    Args:
        url: The URL to fetch.
        associate: If True, run associative reading on text content.

    Returns:
        Formatted string with content (and optionally associations).
    """
    with logfire.span("fetch", url=url, associate=associate) as span:
        original_url = url

        # Step 1: Smart URL rewriting
        url, rewrite_note = await _rewrite_github_url(url)
        if rewrite_note:
            logfire.info("fetch.rewrite: {note}", note=rewrite_note)

        try:
            # Step 2: HTTP fetch
            content_type, body, headers = await _http_fetch(url)
            span.set_attribute("fetch.content_type", content_type)
            span.set_attribute("fetch.size_bytes", len(body))

            # Step 3: Route by content type
            parts = []
            markdown_text = None  # Set if we have text for association

            if content_type == "text/markdown":
                # Tier 1: Native markdown
                text = body.decode("utf-8", errors="replace")
                markdown_text = text
                meta = f"*Markdown from {original_url}"
                if rewrite_note:
                    meta += f" ({rewrite_note})"
                md_tokens = headers.get("x-markdown-tokens")
                if md_tokens:
                    meta += f" — {md_tokens} tokens"
                meta += "*"
                parts.append(text)
                parts.append(f"\n---\n{meta}")

            elif content_type in ("text/html", "application/xhtml+xml"):
                # Tier 2: HTML → markdown
                text = _html_to_markdown(body)
                markdown_text = text
                meta = f"*Converted from HTML ({len(body):,} bytes)"
                if rewrite_note:
                    meta += f" — {rewrite_note}"
                meta += f" — {original_url}*"
                parts.append(text)
                parts.append(f"\n---\n{meta}")

            elif content_type in ("application/json", "application/ld+json"):
                # JSON: pretty-print
                text = body.decode("utf-8", errors="replace")
                try:
                    parsed = json_mod.loads(text)
                    text = json_mod.dumps(parsed, indent=2, ensure_ascii=False)
                except (json_mod.JSONDecodeError, ValueError):
                    pass
                if len(text) > 200_000:
                    text = text[:200_000] + "\n\n[Truncated at 200K characters]"
                markdown_text = text
                parts.append(f"```json\n{text}\n```")
                parts.append(f"\n---\n*JSON from {original_url} ({len(body):,} bytes)*")

            elif content_type.startswith("text/"):
                # Other text: return raw
                text = body.decode("utf-8", errors="replace")
                if len(text) > 200_000:
                    text = text[:200_000] + "\n\n[Truncated at 200K characters]"
                markdown_text = text
                parts.append(text)
                parts.append(f"\n---\n*{content_type} from {original_url}*")

            elif content_type == "application/pdf":
                save_path = _save_binary(body, url, ".pdf")
                parts.append(
                    f"PDF downloaded: {save_path}\n"
                    f"Size: {len(body):,} bytes\n\n"
                    f"Use the Read tool to view it."
                )

            elif content_type.startswith("image/"):
                ext = "." + content_type.split("/")[1].split("+")[0]
                save_path = _save_binary(body, url, ext)
                parts.append(
                    f"Image saved: {save_path}\n"
                    f"Type: {content_type}, Size: {len(body):,} bytes\n\n"
                    f"Use the Read tool to view it."
                )

            else:
                ext = _EXT_MAP.get(content_type, ".bin")
                save_path = _save_binary(body, url, ext)
                parts.append(
                    f"Binary file saved: {save_path}\n"
                    f"Type: {content_type}, Size: {len(body):,} bytes"
                )

            # Step 4: Associative reading (if text content and requested)
            if associate and markdown_text:
                try:
                    memories = await associative_read(
                        markdown_text,
                        source=original_url,
                    )
                    if memories:
                        parts.append(
                            f"\n\n---\n\n## Associations ({len(memories)} memories)\n"
                        )
                        parts.append("\n\n".join(memories))
                except Exception as exc:
                    logfire.warn(
                        "fetch.associate failed: {error}",
                        error=str(exc),
                    )
                    # Content still delivered — associations are best-effort

            span.set_attribute("fetch.has_text", markdown_text is not None)
            return "\n".join(parts)

        except httpx.HTTPStatusError as e:
            return f"HTTP {e.response.status_code} fetching {url}"
        except httpx.ConnectError:
            return f"Could not connect to {url}"
        except httpx.TimeoutException:
            return f"Timeout fetching {url} (30s limit)"
        except Exception as e:
            return f"Error fetching {url}: {e}"

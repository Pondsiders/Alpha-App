"""CLI entry point for MockAnthropic.

Usage:
    uv run mock-anthropic
    uv run mock-anthropic --host 0.0.0.0
    uv run mock-anthropic --port 9999
    uv run mock-anthropic --host 0.0.0.0 --port 9999
    uv run mock-anthropic --host 0.0.0.0:9999    # combined form

`--port` always wins over a port embedded in `--host`.
"""

import argparse
import sys

import uvicorn

from mock_anthropic import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _split_host(spec: str) -> tuple[str, int | None]:
    """Split `host` or `host:port` into (host, optional port)."""
    if ":" in spec:
        host, _, port_str = spec.rpartition(":")
        return host, int(port_str)
    return spec, None


def main() -> None:
    """Parse arguments and run the MockAnthropic FastAPI app under uvicorn."""
    parser = argparse.ArgumentParser(prog="mock-anthropic")
    _ = parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host to bind (default {DEFAULT_HOST}). Accepts host or host:port.",
    )
    _ = parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to bind (default {DEFAULT_PORT}). Wins over a port in --host.",
    )
    args = parser.parse_args()

    host, host_port = _split_host(args.host)
    port = args.port if args.port is not None else (host_port or DEFAULT_PORT)

    base_url = f"http://{host}:{port}"
    print(f"For a good time, set ANTHROPIC_BASE_URL={base_url}", file=sys.stderr)

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

"""MCP tool servers for alpha_app.

FastMCP definitions that claude dispatches to in-process.
No external MCP server processes — just dict in, dict out.
"""

from .alpha import create_alpha_server

__all__ = ["create_alpha_server"]

"""MCP tool servers for alpha_app.

FastMCP definitions that claude dispatches to in-process.
No external MCP server processes — just dict in, dict out.
"""

from .cortex import create_cortex_server
from .handoff import create_handoff_server

__all__ = ["create_cortex_server", "create_handoff_server"]

"""MCP interface layer (Phase E). See server.py for the FastMCP server + tool set."""
from .server import build_server, main

__all__ = ["build_server", "main"]

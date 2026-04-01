"""finreport-mcp FastMCP server — financial report navigation."""

from __future__ import annotations

import traceback

import uvicorn
from fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route

from .tools.store import DocumentStore
from .tools.tools import register_tools

# ---------------------------------------------------------------------------
# Server identity
# ---------------------------------------------------------------------------

_server_host: str = "0.0.0.0"
_server_port: int = 8002

# ---------------------------------------------------------------------------
# Instructions (bilingual EN + ZH)
# ---------------------------------------------------------------------------

_HEADER = """\
┌─────────────────────────────────────────┐
 finreport-mcp — Financial Report Reader
└─────────────────────────────────────────┘
  MinerU content_list.json  →  structured navigation
"""

_INSTRUCTIONS = _HEADER + """
You are connected to finreport-mcp, an MCP server for navigating and
searching annual reports (HK / US listed companies) parsed by MinerU.

## Typical workflow

1. Call **get_document_info** first to learn the report structure.

2. Call **get_outline** to get a heading-based table of contents.

3. Use **get_page_content** to read a specific page in full.

4. Use **search_text** to find pages/elements containing a keyword.

5. Call **get_element_detail** to get the full content of an element
   (especially useful for tables — returns both HTML and Markdown).

6. Call **get_table_image** to visually verify a table's Markdown accuracy.

## Citations

Every element in a tool response includes ``element_index`` and
``citation`` (e.g. ``[Element#42]``). Always include these in your answer
so the user can trace the information back to the PDF source.

## Available tools

- get_document_info  — Document metadata and element type summary
- get_outline        — Hierarchical heading outline
- get_page_content   — All elements on a given page
- search_text        — Keyword search across the document
- get_element_detail — Full content of a single element (with table Markdown)
- get_table_image    — Table screenshot as base64 for visual verification
"""

# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="finreport-mcp Financial Report Navigator",
    instructions=_INSTRUCTIONS,
    auth=None,  # No authentication required
)

# ---------------------------------------------------------------------------
# DocumentStore (shared singleton)
# ---------------------------------------------------------------------------

_store: DocumentStore | None = None


def get_store() -> DocumentStore:
    """Return the global DocumentStore, creating it on first call."""
    global _store
    if _store is None:
        _store = DocumentStore(max_size=10)
    return _store


# ---------------------------------------------------------------------------
# Starlette helper for SSE transport
# ---------------------------------------------------------------------------

def create_starlette_app(mcp_server, *, debug: bool = False) -> Starlette:
    """Create a Starlette app for the SSE transport."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    return Starlette(
        debug=debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )


# ---------------------------------------------------------------------------
# run_server
# ---------------------------------------------------------------------------

def run_server(
    mode: str | None = None,
    port: int = 8002,
    host: str = "0.0.0.0",
    cache_size: int = 10,
) -> None:
    """Start the finreport-mcp server.

    Args:
        mode: Transport mode — 'stdio' (default), 'sse', or 'streamable-http'.
        port: TCP port for HTTP-based transports (default 8002).
        host: Bind address for HTTP-based transports (default 0.0.0.0).
        cache_size: Maximum number of documents held in the LRU cache.
    """
    global _server_host, _server_port

    # (Re-)initialise the store with the requested cache size
    global _store
    _store = DocumentStore(max_size=cache_size)
    mcp_server = mcp._mcp_server

    try:
        if mode == "sse":
            _server_host = host
            _server_port = port
            starlette_app = create_starlette_app(mcp_server, debug=True)
            uvicorn.run(starlette_app, host=host, port=port)
        elif mode == "streamable-http":
            _server_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
            _server_port = port
            http_app = mcp.http_app()
            uvicorn.run(http_app, host=host, port=port)
        else:
            mcp.run(mode or "stdio")
    except Exception as e:
        print(f"\n❌ Server exited with error: {e}", flush=True)
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Register tools (module-level, mirrors mcp/server.py pattern)
# ---------------------------------------------------------------------------

register_tools(mcp, get_store)

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
你已连接到 finreport-mcp，这是一个用于导航和搜索由 MinerU 解析的
上市公司年报（港股/美股）的 MCP 服务器。

## Typical workflow / 典型工作流

1. Call **get_document_info** first to learn the report structure.
   首先调用 get_document_info，了解报告结构（总页数、元素类型分布等）。

2. Call **get_outline** to get a heading-based table of contents.
   调用 get_outline 获取基于标题的目录结构。

3. Use **get_page_content** to read a specific page in full.
   使用 get_page_content 读取特定页面的全部元素。

4. Use **search_text** to find pages/elements containing a keyword.
   使用 search_text 搜索包含关键词的页面或元素。

5. Call **get_element_detail** to get the full content of an element
   (especially useful for tables — returns both HTML and Markdown).
   调用 get_element_detail 获取元素完整内容
   （对表格尤其有用——同时返回 HTML 和 Markdown）。

6. Call **get_table_image** to visually verify a table's Markdown accuracy.
   调用 get_table_image 目视核验表格 Markdown 转换精度。

## Citations / 引用

Every element in a tool response includes ``element_index`` and
``citation`` (e.g. ``[元素#42]``). Always include these in your answer
so the user can trace the information back to the PDF source.
每个工具响应中的元素都包含 ``element_index`` 和 ``citation``
（如 ``[元素#42]``）。回答时请始终附上引用，方便用户追溯 PDF 原文。

## Available tools / 可用工具

- get_document_info  — Document metadata and element type summary
                       文档元数据及元素类型统计
- get_outline        — Hierarchical heading outline
                       层级标题大纲
- get_page_content   — All elements on a given page
                       指定页面的全部元素
- search_text        — Keyword search across the document
                       全文关键词搜索
- get_element_detail — Full content of a single element (with table Markdown)
                       单个元素的完整内容（含表格 Markdown）
- get_table_image    — Table screenshot as base64 for visual verification
                       表格截图（base64）用于目视核验
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
    """Create a Starlette app for the SSE transport.

    创建用于 SSE 传输的 Starlette 应用。
    """
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

    启动 finreport-mcp 服务器。

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
        print(f"\n❌ 服务异常退出: {e}", flush=True)
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Register tools (module-level, mirrors mcp/server.py pattern)
# ---------------------------------------------------------------------------

register_tools(mcp, get_store)

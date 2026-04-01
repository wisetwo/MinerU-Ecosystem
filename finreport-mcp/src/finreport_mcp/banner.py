"""Startup banner for finreport-mcp — printed to stderr on launch."""

import sys

_BANNER = r"""
  ┌──────────────────────────────────────────────────────────────┐
  │   __ _                         _                 _           │
  │  / _(_)_ __  _ __ ___ _ __   _| |_      _ __ ___| |__       │
  │ | |_| | '_ \| '__/ _ \ '_ \ / _` \ \ /\ / / '__/ _ \ '_ \   │
  │ |  _| | | | | | |  __/ |_) | (_| |\ V  V /| | |  __/ |_) |  │
  │ |_| |_|_| |_|_|  \___| .__/ \__,_| \_/\_/ |_|  \___|_.__/   │
  │                       |_|                                     │
  │              m  c  p                                          │
  └──────────────────────────────────────────────────────────────┘
"""

_INFO_TEMPLATE = """\
  Transport  : {transport}
  Host       : {host}
  Cache size : {cache_size} reports (LRU)

  Tools available:
    • get_document_info  — Document metadata & element type summary
    • get_outline        — Hierarchical heading outline
    • get_page_content   — All elements on a given page
    • search_text        — Full-text keyword search
    • get_element_detail — Full content of a single element (+ table Markdown)
    • get_table_image    — Table screenshot (base64) for visual verification

  Powered by MinerU  ·  https://mineru.net/
"""


def print_banner(
    transport: str = "stdio",
    host: str = "",
    cache_size: int = 10,
) -> None:
    """Print the finreport-mcp startup banner to stderr.

    Always writes to stderr so it never corrupts the stdio MCP wire.
    始终写入 stderr，确保不污染 stdio MCP 通道。
    """
    if transport in ("streamable-http", "sse"):
        display_host = host or "0.0.0.0"
    else:
        display_host = "— (stdio, no network port)"

    info = _INFO_TEMPLATE.format(
        transport=transport,
        host=display_host,
        cache_size=cache_size,
    )

    print(_BANNER, file=sys.stderr)
    print(info, file=sys.stderr)

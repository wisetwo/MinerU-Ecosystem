# finreport-mcp

An MCP (Model Context Protocol) server for navigating and searching annual reports of listed companies (HK/US markets) parsed by [MinerU](https://mineru.net/).

基于 MinerU 解析财报后输出的 `content_list.json`，构建的 MCP 服务器。让 AI 助手能高效地读取、导航、搜索上市公司年报（港股/美股），并在每条引用中附上元素索引，方便用户在 viewer UI 中追溯 PDF 原文。

---

## Features / 功能特性

- **Document overview** — total pages, element type distribution, `.md` files present
- **Heading outline** — hierarchical H1/H2/H3 table of contents extracted from `text_level` fields
- **Page-level reading** — all elements on any given page with table previews
- **Full-text search** — case-insensitive substring search across text, table captions/footnotes/body, image captions, and list items
- **Element detail** — full content of any element; tables include both raw HTML and GFM Markdown
- **Table image** — retrieve table screenshot as base64 for visual verification of Markdown accuracy

Every tool response includes `element_index` and `citation` (e.g. `[元素#42]`) for tracing back to the PDF source.

---

## Requirements / 环境要求

- Python ≥ 3.10
- [`fastmcp`](https://github.com/jlowin/fastmcp) ≥ 3.1.0
- A report directory produced by MinerU, containing `content_list.json` or `auto_content_list.json`

---

## Installation / 安装

```bash
cd finreport-mcp
pip install -e .
```

---

## Usage / 使用方法

### stdio (default / 默认)

```bash
finreport-mcp
# or explicitly:
finreport-mcp --transport stdio
```

### SSE

```bash
finreport-mcp --transport sse --port 8002
```

### Streamable HTTP

```bash
finreport-mcp --transport streamable-http --port 8002
```

### All options / 所有参数

| Option | Default | Description |
|---|---|---|
| `--transport` / `-t` | `stdio` | Transport: `stdio`, `sse`, `streamable-http` |
| `--port` / `-p` | `8002` | TCP port (HTTP transports only) |
| `--host` | `0.0.0.0` | Bind address (HTTP transports only) |
| `--cache-size` | `10` | Max reports held in LRU cache |

---

## Claude Desktop Configuration / Claude Desktop 配置

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "finreport": {
      "command": "finreport-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

---

## Tools / 工具列表

### `get_document_info(report_dir)`

Call this **first** when starting to analyse a report.

Returns total pages, element count, type distribution, and available `.md` files.

### `get_outline(report_dir)`

Returns a hierarchical outline of H1/H2/H3 headings with element indices and page numbers.

### `get_page_content(report_dir, page_idx)`

Returns all elements on the given page (zero-based). Tables include a 500-byte Markdown preview; use `get_element_detail` for full content.

### `search_text(report_dir, query, page_start=None, page_end=None)`

Case-insensitive keyword search across the document. Optionally restrict to a page range.

### `get_element_detail(report_dir, element_index)`

Returns the complete content of a single element. For tables: includes both `table_body_html` and `table_body_markdown`.

### `get_table_image(report_dir, element_index)`

Returns the table screenshot as base64-encoded image data for visual verification.

---

## Report Directory Format / 报告目录格式

The `report_dir` should be a MinerU output directory containing:

```
report_dir/
├── content_list.json       # or auto_content_list.json
├── *.md                    # Markdown output files
└── images/                 # Table/figure images
    ├── *.jpg
    └── *.png
```

### Example element types in `content_list.json`

| Type | Fields used |
|---|---|
| `text` / `header` | `text`, `text_level`, `page_idx`, `bbox` |
| `table` | `table_body` (HTML), `table_caption`, `table_footnote`, `img_path`, `page_idx`, `bbox` |
| `image` | `img_caption`, `img_path`, `page_idx`, `bbox` |
| `list` / `list_item` | `list_content`, `page_idx`, `bbox` |
| `page_number` | `text`, `page_idx` |

---

## Demo Report Paths / 示例报告路径

```
sdk/python/tests/pdf-demo-output/拼多多-PDD-2024年年报-demo/
sdk/python/tests/pdf-demo-output/小米集团-1810-2024年年报-demo/
```

---

## Architecture / 架构

```
finreport-mcp/
├── pyproject.toml
└── src/
    └── finreport_mcp/
        ├── __init__.py          # __version__
        ├── banner.py            # stderr startup info
        ├── cli.py               # argparse entry point
        ├── server.py            # FastMCP instance + run_server()
        └── tools/
            ├── __init__.py
            ├── store.py         # DocumentStore (thread-safe LRU cache)
            ├── table_converter.py  # HTML table → GFM Markdown (stdlib only)
            └── tools.py         # register_tools(mcp, get_store_fn)
```

- **`DocumentStore`** uses `collections.OrderedDict` + `threading.RLock` for thread-safe LRU caching. File I/O is performed outside the lock to avoid blocking concurrent cache hits (double-check on insert).
- **`table_converter`** uses only Python stdlib (`html.parser`) — no third-party dependencies. Handles `colspan` and `rowspan` via grid expansion.

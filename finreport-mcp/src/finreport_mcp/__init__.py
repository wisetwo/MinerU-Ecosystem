"""finreport-mcp — Financial report navigation MCP server powered by MinerU.

A Model Context Protocol server for efficiently reading, navigating, and
searching annual reports (HK/US listed companies) parsed by MinerU.
Each tool response includes element indices for tracing back to the PDF
source in a viewer UI.

基于 MinerU 解析的财报 content_list.json，构建的 MCP 服务器。
让 AI 助手能高效读取、导航、搜索上市公司年报（港股/美股），
每条引用均附带元素索引，方便用户在 viewer UI 中追溯 PDF 原文。
"""

__version__ = "0.1.0"

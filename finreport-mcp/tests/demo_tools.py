#!/usr/bin/env python3
"""
finreport-mcp 工具效果演示脚本

用法：
    python tests/demo_tools.py
    python tests/demo_tools.py /path/to/report-dir
    python tests/demo_tools.py --tool get_overview
    python tests/demo_tools.py --tool search_text --query revenue
    python tests/demo_tools.py --tool read_page --page 3
    python tests/demo_tools.py --tool read_elements --start 15 --end 25

默认 report_dir 使用拼多多年报 demo 目录（自动查找）。
"""
from __future__ import annotations

import asyncio
import sys
import os
import argparse
from pathlib import Path

# 让脚本在项目根目录直接运行时能找到 src/
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT / "src"))

from finreport_mcp.tools.store import DocumentStore
from finreport_mcp.tools import tools as tools_module

# ─────────────────────────────────────────────
# Default report directory auto-detection
# ─────────────────────────────────────────────

_DEMO_CANDIDATES = [
    # 相对于本项目根目录
    _ROOT.parent / "sdk/python/tests/pdf-demo-output/拼多多-PDD-2024年年报-demo",
    _ROOT.parent / "sdk/python/tests/pdf-demo-output/小米集团-1810-2024年年报-demo",
]


def find_default_report_dir() -> str:
    for candidate in _DEMO_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    # 最后兜底：找 pdf-demo-output 下第一个子目录
    demo_root = _ROOT.parent / "sdk/python/tests/pdf-demo-output"
    if demo_root.exists():
        subdirs = [d for d in demo_root.iterdir() if d.is_dir()]
        if subdirs:
            return str(subdirs[0])
    return ""


# ─────────────────────────────────────────────
# Fake MCP server (registers tools into dict)
# ─────────────────────────────────────────────

class _FakeMCP:
    """Minimal shim that mimics FastMCP.tool() decorator for local testing."""

    def __init__(self):
        self._tools: dict[str, object] = {}

    def tool(self):
        def decorator(fn):
            self._tools[fn.__name__] = fn
            return fn
        return decorator

    def get(self, name: str):
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

DIVIDER = "─" * 70
HEADER_FMT = "\n{div}\n  工具: {name}\n{div}"

MAX_TEXT_DISPLAY = 3000  # chars before truncating long string fields


def _display_result(result: dict) -> None:
    if "error" in result:
        print(f"  ⚠️  错误: {result['error']}")
        return

    for key, value in result.items():
        if isinstance(value, str):
            if len(value) > MAX_TEXT_DISPLAY:
                print(f"\n[{key}]:\n{value[:MAX_TEXT_DISPLAY]}\n… (已截断，共 {len(value)} 字符)")
            elif "\n" in value:
                print(f"\n[{key}]:\n{value}")
            else:
                print(f"  {key}: {value}")
        elif isinstance(value, (int, float, bool)):
            print(f"  {key}: {value}")
        elif isinstance(value, list):
            if len(value) == 0:
                print(f"  {key}: []")
            elif len(str(value)) < 120:
                print(f"  {key}: {value}")
            else:
                print(f"  {key}: [{len(value)} items] {str(value[:3])[:-1]}, ...]")
        else:
            print(f"  {key}: {value}")


async def run_demo(report_dir: str, only_tool: str | None, extra_args: dict) -> None:
    store = DocumentStore()

    fake_mcp = _FakeMCP()
    tools_module.register_tools(fake_mcp, lambda: store)

    print(f"\n{'═'*70}")
    print(f"  finreport-mcp 工具演示")
    print(f"  报告目录: {report_dir}")
    print(f"  已注册工具: {', '.join(fake_mcp.list_tools())}")
    print(f"{'═'*70}")

    async def call(tool_name: str, **kwargs) -> dict:
        fn = fake_mcp.get(tool_name)
        if fn is None:
            return {"error": f"工具 '{tool_name}' 未找到"}
        return await fn(report_dir=report_dir, **kwargs)

    # ── Helper to print section ──────────────────────────
    def section(name: str):
        print(HEADER_FMT.format(div=DIVIDER, name=name))

    # ────────────────────────────────────────────────────
    # Demo sequence
    # ────────────────────────────────────────────────────

    demos_to_run = only_tool  # None means run all

    # 1. get_overview
    if not demos_to_run or demos_to_run == "get_overview":
        section("get_overview")
        result = await call("get_overview")
        _display_result(result)

    # 2. get_outline
    if not demos_to_run or demos_to_run == "get_outline":
        section("get_outline")
        result = await call("get_outline")
        _display_result(result)

    # 3. read_page
    page_idx = extra_args.get("page", 0)
    if not demos_to_run or demos_to_run == "read_page":
        section(f"read_page  (page_idx={page_idx})")
        result = await call("read_page", page_idx=page_idx)
        _display_result(result)

    # 4. read_elements — pick element range from outline if available
    start_idx = extra_args.get("start", 0)
    end_idx = extra_args.get("end", 20)
    if not demos_to_run or demos_to_run == "read_elements":
        section(f"read_elements  (start={start_idx}, end={end_idx})")
        result = await call("read_elements", start_index=start_idx, end_index=end_idx)
        _display_result(result)

    # 5. search_text
    query = extra_args.get("query", "revenue")
    if not demos_to_run or demos_to_run == "search_text":
        section(f"search_text  (query='{query}')")
        result = await call("search_text", query=query)
        _display_result(result)

    # 6. get_element_detail — find first table
    if not demos_to_run or demos_to_run == "get_element_detail":
        section("get_element_detail  (first table element)")
        # Find first table element
        content = store.get(report_dir)
        table_idx = next(
            (i for i, e in enumerate(content) if e.get("type") == "table"), None
        )
        if table_idx is not None:
            result = await call("get_element_detail", element_index=table_idx)
            _display_result(result)
        else:
            print("  （文档中未找到 table 元素）")

    # 7. get_table_image — only if table exists
    if not demos_to_run or demos_to_run == "get_table_image":
        section("get_table_image  (first table with img_path)")
        content = store.get(report_dir)
        img_table_idx = next(
            (i for i, e in enumerate(content)
             if e.get("type") == "table" and e.get("img_path")),
            None,
        )
        if img_table_idx is not None:
            result = await call("get_table_image", element_index=img_table_idx)
            # Don't print base64 data, just metadata
            display = {k: v for k, v in result.items() if k != "data_base64"}
            if "data_base64" in result:
                display["data_base64"] = f"<base64, {len(result['data_base64'])} chars>"
            _display_result(display)
        else:
            print("  （文档中未找到带图片的 table 元素）")

    print(f"\n{'═'*70}\n  演示完成\n{'═'*70}\n")


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="finreport-mcp 工具演示",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "report_dir",
        nargs="?",
        default="",
        help="报告目录路径（可选，默认自动检测 demo 目录）",
    )
    parser.add_argument(
        "--tool", "-t",
        choices=[
            "get_overview", "get_outline", "read_page", "read_elements",
            "search_text", "get_element_detail", "get_table_image",
        ],
        help="只运行指定工具（默认全部运行）",
    )
    parser.add_argument("--query", "-q", default="revenue", help="search_text 的搜索词")
    parser.add_argument("--page", "-p", type=int, default=0, help="read_page 的页码（0-based）")
    parser.add_argument("--start", type=int, default=0, help="read_elements 的起始元素索引")
    parser.add_argument("--end", type=int, default=20, help="read_elements 的结束元素索引")

    args = parser.parse_args()

    report_dir = args.report_dir or find_default_report_dir()
    if not report_dir:
        print("错误：未找到默认 demo 目录，请手动指定 report_dir 参数。")
        sys.exit(1)

    if not Path(report_dir).exists():
        print(f"错误：目录不存在: {report_dir}")
        sys.exit(1)

    extra_args = {
        "query": args.query,
        "page": args.page,
        "start": args.start,
        "end": args.end,
    }

    asyncio.run(run_demo(report_dir, args.tool, extra_args))


if __name__ == "__main__":
    main()

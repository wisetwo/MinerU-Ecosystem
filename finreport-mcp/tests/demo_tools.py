#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finreport-mcp tool demo script

Usage:
    python3 tests/demo_tools.py
    python3 tests/demo_tools.py /path/to/report-dir
    python3 tests/demo_tools.py --tool get_overview
    python3 tests/demo_tools.py --tool search_text --query revenue
    python3 tests/demo_tools.py --tool read_page --page 3
    python3 tests/demo_tools.py --tool read_elements --start 15 --end 25

The default report_dir is auto-detected from the sibling sdk demo output directory.
"""
from __future__ import annotations

import asyncio
import sys
import argparse
from pathlib import Path

# Allow running directly from the project root: python3 tests/demo_tools.py
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT / "src"))

from finreport_mcp.tools.store import DocumentStore
from finreport_mcp.tools import tools as tools_module

# ---------------------------------------------------------------------------
# Default report directory auto-detection
# ---------------------------------------------------------------------------

_DEMO_CANDIDATES = [
    # Paths relative to the MinerU-Ecosystem repo root
    _ROOT.parent / "sdk/python/tests/pdf-demo-output/\u62fc\u591a\u591a-PDD-2024\u5e74\u5e74\u62a5-demo",
    _ROOT.parent / "sdk/python/tests/pdf-demo-output/\u5c0f\u7c73\u96c6\u56e2-1810-2024\u5e74\u5e74\u62a5-demo",
]


def find_default_report_dir() -> str:
    for candidate in _DEMO_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    # Fallback: first subdirectory found under pdf-demo-output
    demo_root = _ROOT.parent / "sdk/python/tests/pdf-demo-output"
    if demo_root.exists():
        subdirs = [d for d in demo_root.iterdir() if d.is_dir()]
        if subdirs:
            return str(subdirs[0])
    return ""


# ---------------------------------------------------------------------------
# Fake MCP server (registers tools into a plain dict for local testing)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

DIVIDER = "-" * 70
HEADER_FMT = "\n{div}\n  Tool: {name}\n{div}"

MAX_TEXT_DISPLAY = 3000  # chars before truncating long string fields


def _display_result(result: dict) -> None:
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return

    for key, value in result.items():
        if isinstance(value, str):
            if len(value) > MAX_TEXT_DISPLAY:
                print(f"\n[{key}]:\n{value[:MAX_TEXT_DISPLAY]}\n... (truncated, total {len(value)} chars)")
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

    print(f"\n{'='*70}")
    print(f"  finreport-mcp tool demo")
    print(f"  Report dir: {report_dir}")
    print(f"  Registered tools: {', '.join(fake_mcp.list_tools())}")
    print(f"{'='*70}")

    async def call(tool_name: str, **kwargs) -> dict:
        fn = fake_mcp.get(tool_name)
        if fn is None:
            return {"error": f"tool '{tool_name}' not found"}
        return await fn(report_dir=report_dir, **kwargs)

    def section(name: str):
        print(HEADER_FMT.format(div=DIVIDER, name=name))

    # 1. get_overview
    if not only_tool or only_tool == "get_overview":
        section("get_overview")
        result = await call("get_overview")
        _display_result(result)

    # 2. get_outline
    if not only_tool or only_tool == "get_outline":
        section("get_outline")
        result = await call("get_outline")
        _display_result(result)

    # 3. read_page
    page_idx = extra_args.get("page", 0)
    if not only_tool or only_tool == "read_page":
        section(f"read_page  (page_idx={page_idx})")
        result = await call("read_page", page_idx=page_idx)
        _display_result(result)

    # 4. read_elements
    start_idx = extra_args.get("start", 0)
    end_idx = extra_args.get("end", 20)
    if not only_tool or only_tool == "read_elements":
        section(f"read_elements  (start={start_idx}, end={end_idx})")
        result = await call("read_elements", start_index=start_idx, end_index=end_idx)
        _display_result(result)

    # 5. search_text
    query = extra_args.get("query", "revenue")
    if not only_tool or only_tool == "search_text":
        section(f"search_text  (query='{query}')")
        result = await call("search_text", query=query)
        _display_result(result)

    # 6. get_element_detail — use first table element
    if not only_tool or only_tool == "get_element_detail":
        section("get_element_detail  (first table element)")
        content = store.get(report_dir)
        table_idx = next(
            (i for i, e in enumerate(content) if e.get("type") == "table"), None
        )
        if table_idx is not None:
            result = await call("get_element_detail", element_index=table_idx)
            _display_result(result)
        else:
            print("  (no table element found in document)")

    # 7. get_table_image — use first table with img_path
    if not only_tool or only_tool == "get_table_image":
        section("get_table_image  (first table with img_path)")
        content = store.get(report_dir)
        img_table_idx = next(
            (i for i, e in enumerate(content)
             if e.get("type") == "table" and e.get("img_path")),
            None,
        )
        if img_table_idx is not None:
            result = await call("get_table_image", element_index=img_table_idx)
            # Summarise base64 blob rather than printing it
            display = {k: v for k, v in result.items() if k != "data_base64"}
            if "data_base64" in result:
                display["data_base64"] = f"<base64, {len(result['data_base64'])} chars>"
            _display_result(display)
        else:
            print("  (no table with img_path found in document)")

    print(f"\n{'='*70}\n  Demo complete\n{'='*70}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="finreport-mcp tool demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "report_dir",
        nargs="?",
        default="",
        help="Path to a MinerU report directory (auto-detected if omitted)",
    )
    parser.add_argument(
        "--tool", "-t",
        choices=[
            "get_overview", "get_outline", "read_page", "read_elements",
            "search_text", "get_element_detail", "get_table_image",
        ],
        help="Run only this tool (default: run all)",
    )
    parser.add_argument("--query", "-q", default="revenue", help="Query string for search_text")
    parser.add_argument("--page", "-p", type=int, default=0, help="Page index for read_page (0-based)")
    parser.add_argument("--start", type=int, default=0, help="Start element index for read_elements")
    parser.add_argument("--end", type=int, default=20, help="End element index for read_elements")

    args = parser.parse_args()

    report_dir = args.report_dir or find_default_report_dir()
    if not report_dir:
        print("Error: no default demo directory found. Please pass a report_dir argument.")
        sys.exit(1)

    if not Path(report_dir).exists():
        print(f"Error: directory does not exist: {report_dir}")
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

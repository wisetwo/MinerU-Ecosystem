# -*- coding: utf-8 -*-
"""MCP tool registration for finreport — financial report navigation.

Agent-first philosophy: every tool returns human-readable text (markdown),
not structured JSON arrays. Think of it like a coding agent reading files:
  - get_overview   → understand the document at a glance
  - get_outline    → indented table of contents
  - read_page      → rendered markdown of a page
  - read_elements  → rendered markdown of an element range (like read_file)
  - search_text    → grep-style results with context lines
  - get_element_detail → single element detail (clean, minimal)
  - get_table_image    → raw image for visual verification
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional, Tuple

from fastmcp import FastMCP
from pydantic import Field

from .store import DocumentStore
from .table_converter import html_table_to_markdown

# ---------------------------------------------------------------------------
# Field descriptors (reusable across tools)
# ---------------------------------------------------------------------------

_REPORT_DIR_FIELD = Field(
    description=(
        "Absolute path to the report directory produced by MinerU. "
        "The directory must contain a content_list.json (or auto_content_list.json) file."
    )
)

_PAGE_IDX_FIELD = Field(
    description="Zero-based page index to retrieve (e.g. 0 for the first page)."
)

_ELEMENT_INDEX_FIELD = Field(
    description="Zero-based index of the element within the document's content list."
)

_START_INDEX_FIELD = Field(
    description="Zero-based start element index (inclusive)."
)

_END_INDEX_FIELD = Field(
    description="Zero-based end element index (inclusive)."
)

_QUERY_FIELD = Field(
    description=(
        "Case-insensitive substring to search for across the document. "
        "Matched against text, table captions/footnotes/body, image captions, and list items."
    )
)

_PAGE_START_FIELD = Field(
    description=(
        "Restrict search to pages with index >= page_start (inclusive, zero-based). "
        "Pass null to search from the beginning."
    )
)

_PAGE_END_FIELD = Field(
    description=(
        "Restrict search to pages with index <= page_end (inclusive, zero-based). "
        "Pass null to search to the end."
    )
)


# ---------------------------------------------------------------------------
# Core rendering helpers
# ---------------------------------------------------------------------------


def _elem_one_liner(elem: Dict[str, Any]) -> str:
    """Render a single element as a one-line text snippet (for search context)."""
    etype = elem.get("type", "")
    if etype in ("text", "header"):
        level = elem.get("text_level")
        text = elem.get("text", "").strip()
        if level:
            return "#" * int(level) + " " + text
        return text
    elif etype == "table":
        captions = elem.get("table_caption", [])
        if captions:
            return f"**Table: {'; '.join(captions)}**"
        # First line of markdown table
        md = html_table_to_markdown(elem.get("table_body", ""))
        first_line = md.split("\n")[0] if md else ""
        return first_line
    elif etype in ("list", "list_item"):
        content = elem.get("list_content", elem.get("text", ""))
        # Just first line
        return content.split("\n")[0] if content else ""
    elif etype == "image":
        captions = elem.get("img_caption", [])
        if captions:
            return f"[图片: {'; '.join(captions)}]"
        return "[图片]"
    elif etype == "page_number":
        return ""  # skip
    return elem.get("text", "").strip()


def _render_elem(
    index: int,
    elem: Dict[str, Any],
    anchor_comments: bool = True,
) -> str:
    """Render a single element as markdown text (multi-line for tables/lists)."""
    etype = elem.get("type", "")
    pg = elem.get("page_idx", "?")

    if etype == "page_number":
        return ""  # always skip page number elements

    anchor = f"<!-- elem {index}, p.{pg} -->\n" if anchor_comments else ""

    if etype in ("text", "header"):
        level = elem.get("text_level")
        text = elem.get("text", "").strip()
        if level:
            body = "#" * int(level) + " " + text
        else:
            body = text
        return anchor + body

    elif etype == "table":
        lines: List[str] = []
        for cap in elem.get("table_caption", []):
            lines.append(f"**{cap}**")
        md_table = html_table_to_markdown(elem.get("table_body", ""))
        lines.append(md_table)
        for fn in elem.get("table_footnote", []):
            lines.append(f"*{fn}*")
        return anchor + "\n".join(lines)

    elif etype in ("list", "list_item"):
        content = elem.get("list_content", elem.get("text", ""))
        return anchor + content

    elif etype == "image":
        captions = elem.get("img_caption", [])
        if captions:
            return anchor + f"[图片: {'; '.join(captions)}]"
        return ""  # no caption → skip silently

    return anchor + elem.get("text", "").strip()


def _render_elements_as_markdown(
    elements_with_indices: List[Tuple[int, Dict[str, Any]]],
    anchor_comments: bool = True,
) -> str:
    """Render a list of (index, elem) pairs as a flowing markdown document."""
    parts = []
    for i, elem in elements_with_indices:
        rendered = _render_elem(i, elem, anchor_comments=anchor_comments)
        if rendered.strip():
            parts.append(rendered)
    return "\n\n".join(parts)


def _build_outline_items(content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract heading elements from content list as outline items."""
    outline = []
    for i, elem in enumerate(content):
        level = elem.get("text_level")
        if level is None:
            continue
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            continue
        text = elem.get("text", "").strip()
        if not text:
            continue
        outline.append({
            "element_index": i,
            "level": level_int,
            "text": text,
            "page_idx": elem.get("page_idx"),
        })
    return outline


def _format_outline_text(outline_items: List[Dict[str, Any]]) -> str:
    """Format outline items as indented text with navigation anchors."""
    lines = []
    for item in outline_items:
        level = item["level"]
        indent = "  " * (level - 1)
        hashes = "#" * level
        lines.append(
            f"{indent}{hashes} {item['text']} "
            f"(p.{item['page_idx']}) [elem {item['element_index']}]"
        )
    return "\n".join(lines)


def _elem_search_text(elem: Dict[str, Any]) -> str:
    """Return all searchable text from an element (for keyword matching)."""
    parts = []
    t = elem.get("text", "")
    if t:
        parts.append(t)
    for cap in elem.get("table_caption", []):
        parts.append(cap)
    for fn in elem.get("table_footnote", []):
        parts.append(fn)
    tb = elem.get("table_body", "")
    if tb:
        parts.append(tb)
    for cap in elem.get("img_caption", []):
        parts.append(cap)
    lc = elem.get("list_content", "")
    if lc:
        parts.append(lc)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP, get_store_fn: Callable[[], DocumentStore]) -> None:
    """Register all finreport tools onto *mcp*, injecting *get_store_fn*.

    *get_store_fn* is called on every tool invocation so that the store
    instance can be replaced (e.g. with a different cache size) after
    module initialisation without re-registering tools.
    """

    # ------------------------------------------------------------------
    # Tool 1 — get_overview
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_overview(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
    ) -> Dict[str, Any]:
        """Return a human-readable overview of the financial report.

        Combines document metadata with the full table of contents in one call.
        Call this FIRST when starting to analyse a report — it gives you
        everything you need to understand the document structure and navigate it.

        The returned ``overview_text`` is a markdown string you can read directly:
          - File name and page/element counts
          - Type breakdown (how many text, table, list elements, etc.)
          - Full hierarchical table of contents with page numbers and element indices

        Returns:
            {
                "overview_text": "<markdown string>",
                "total_pages": <int>,
                "total_elements": <int>,
            }
        """
        try:
            content = get_store_fn().get(report_dir)
            abs_dir = str(Path(report_dir).resolve())

            total_pages = max((e.get("page_idx", 0) for e in content), default=0) + 1

            type_counts: Dict[str, int] = {}
            for elem in content:
                t = elem.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1

            md_files = sorted(p.name for p in Path(abs_dir).glob("*.md"))
            file_line = f"文件：{', '.join(md_files)}" if md_files else f"目录：{abs_dir}"

            # Type summary line
            type_summary = ", ".join(
                f"{v} {k}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
            )
            stats_line = (
                f"共 {total_pages} 页，{len(content)} 个元素（{type_summary}）"
            )

            # Outline
            outline_items = _build_outline_items(content)
            outline_text = _format_outline_text(outline_items)

            overview_text = "\n".join([
                file_line,
                stats_line,
                "",
                "## 目录",
                "",
                outline_text if outline_text else "（未找到章节标题）",
            ])

            return {
                "overview_text": overview_text,
                "total_pages": total_pages,
                "total_elements": len(content),
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 2 — get_outline
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_outline(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
    ) -> Dict[str, Any]:
        """Return the table of contents as a readable indented text.

        Each heading line shows:
          - Indentation level (2 spaces per level)
          - Heading text
          - Page number: ``(p.N)``
          - Element anchor: ``[elem N]`` — use this index with ``read_elements``

        Example output::

            # PART I (p.3) [elem 15]
              ## Item 1. Business (p.3) [elem 18]
              ## Item 1A. Risk Factors (p.5) [elem 25]
                ### Risks Related to Our Business (p.5) [elem 27]

        Returns:
            {
                "outline_text": "<indented text>",
                "total_headings": <int>,
            }
        """
        try:
            content = get_store_fn().get(report_dir)
            outline_items = _build_outline_items(content)
            outline_text = _format_outline_text(outline_items)
            return {
                "outline_text": outline_text,
                "total_headings": len(outline_items),
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 3 — read_page
    # ------------------------------------------------------------------

    @mcp.tool()
    async def read_page(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        page_idx: Annotated[int, _PAGE_IDX_FIELD],
    ) -> Dict[str, Any]:
        """Return the content of a single page as rendered markdown.

        Tables are converted to GFM markdown. Page number elements are skipped.
        Each element is preceded by a ``<!-- elem N, p.X -->`` anchor comment
        so you can refer to specific elements by index.

        Returns:
            {
                "content": "<markdown string>",
                "page_idx": <int>,
                "total_pages": <int>,
                "element_count": <int>,
            }
        """
        try:
            elements, total_pages = get_store_fn().get_page_elements(report_dir, page_idx)
            content_text = _render_elements_as_markdown(elements, anchor_comments=True)
            non_pnum = [(i, e) for i, e in elements if e.get("type") != "page_number"]
            return {
                "content": content_text,
                "page_idx": page_idx,
                "total_pages": total_pages,
                "element_count": len(non_pnum),
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 4 — read_elements
    # ------------------------------------------------------------------

    @mcp.tool()
    async def read_elements(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        start_index: Annotated[int, _START_INDEX_FIELD],
        end_index: Annotated[int, _END_INDEX_FIELD],
    ) -> Dict[str, Any]:
        """Read a range of elements as rendered markdown, like reading lines of a file.

        Use element indices from ``get_outline`` (``[elem N]`` anchors) or
        ``search_text`` results to navigate to specific sections.

        Each element is preceded by a ``<!-- elem N, p.X -->`` anchor comment.
        Tables are rendered as GFM markdown. Page number elements are skipped.

        Returns:
            {
                "content": "<markdown string>",
                "element_range": [start, end],
                "total_elements_in_doc": <int>,
            }
        """
        try:
            store = get_store_fn()
            pairs = store.get_elements_range(report_dir, start_index, end_index)
            content_text = _render_elements_as_markdown(pairs, anchor_comments=True)
            total = len(store.get(report_dir))
            actual_end = pairs[-1][0] if pairs else start_index
            return {
                "content": content_text,
                "element_range": [start_index, actual_end],
                "total_elements_in_doc": total,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 5 — search_text
    # ------------------------------------------------------------------

    @mcp.tool()
    async def search_text(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        query: Annotated[str, _QUERY_FIELD],
        page_start: Annotated[Optional[int], _PAGE_START_FIELD] = None,
        page_end: Annotated[Optional[int], _PAGE_END_FIELD] = None,
    ) -> Dict[str, Any]:
        """Search for a keyword across the document, returning grep-style results.

        For each match, one context element before and after the hit is shown
        (page_number elements are skipped when looking for context).
        The matching element line is prefixed with ``>>`` for easy scanning.

        Format::

            [p.5, elem 27]    ## Risks Related to Our Business
            [p.5, elem 28] >> The following risk factors may adversely affect...
            [p.5, elem 29]    Our success depends on our ability to attract...

        Returns:
            {
                "matches_text": "<grep-style text>",
                "total_matches": <int>,
                "query": "<query>",
            }
        """
        try:
            content = get_store_fn().get(report_dir)
            needle = query.lower()

            # Find matching indices
            hit_indices: List[int] = []
            for i, elem in enumerate(content):
                pg = elem.get("page_idx", 0)
                if page_start is not None and pg < page_start:
                    continue
                if page_end is not None and pg > page_end:
                    continue
                if needle in _elem_search_text(elem).lower():
                    hit_indices.append(i)

            if not hit_indices:
                return {
                    "matches_text": f"No matches found for: {query}",
                    "total_matches": 0,
                    "query": query,
                }

            # Build display groups with context, merging overlapping windows
            def _prev_non_pnum(idx: int) -> Optional[int]:
                """Return index of previous non-page_number element, or None."""
                j = idx - 1
                while j >= 0:
                    if content[j].get("type") != "page_number":
                        return j
                    j -= 1
                return None

            def _next_non_pnum(idx: int) -> Optional[int]:
                """Return index of next non-page_number element, or None."""
                j = idx + 1
                while j < len(content):
                    if content[j].get("type") != "page_number":
                        return j
                    j += 1
                return None

            # Collect groups (hit_idx, before_idx_or_None, after_idx_or_None)
            groups: List[Tuple[int, Optional[int], Optional[int]]] = []
            for hi in hit_indices:
                groups.append((hi, _prev_non_pnum(hi), _next_non_pnum(hi)))

            # Render each group
            snippets: List[str] = []
            for hit_idx, before_idx, after_idx in groups:
                lines: List[str] = []

                def _prefix(idx: int, is_hit: bool) -> str:
                    pg = content[idx].get("page_idx", "?")
                    marker = ">>" if is_hit else "  "
                    snippet = _elem_one_liner(content[idx])
                    return f"[p.{pg}, elem {idx}] {marker} {snippet}"

                if before_idx is not None:
                    lines.append(_prefix(before_idx, False))
                lines.append(_prefix(hit_idx, True))
                if after_idx is not None:
                    lines.append(_prefix(after_idx, False))

                snippets.append("\n".join(lines))

            matches_text = "\n\n".join(snippets)

            return {
                "matches_text": matches_text,
                "total_matches": len(hit_indices),
                "query": query,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 6 — get_element_detail
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_element_detail(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        element_index: Annotated[int, _ELEMENT_INDEX_FIELD],
    ) -> Dict[str, Any]:
        """Return the full content of a single element by its index.

        Unlike ``read_elements``, this returns structured data (not rendered
        markdown) so you can inspect element type, page, and raw content fields.

        For tables, ``table_body_markdown`` is the GFM-rendered version.
        ``table_body_html`` is omitted by default (only the markdown is returned).

        Noise fields (bbox, citation) are excluded.

        Returns (type-dependent fields):
            text/header:  { type, page_idx, text, text_level? }
            table:        { type, page_idx, table_caption, table_body_markdown, table_footnote, img_path? }
            list:         { type, page_idx, list_content }
            image:        { type, page_idx, img_caption, img_path? }
        """
        try:
            elem = get_store_fn().get_element(report_dir, element_index)
            etype = elem.get("type", "unknown")

            result: Dict[str, Any] = {
                "element_index": element_index,
                "type": etype,
                "page_idx": elem.get("page_idx"),
            }

            if etype == "table":
                result["table_caption"] = elem.get("table_caption", [])
                result["table_body_markdown"] = html_table_to_markdown(
                    elem.get("table_body", "")
                )
                result["table_footnote"] = elem.get("table_footnote", [])
                if elem.get("img_path"):
                    result["img_path"] = elem["img_path"]

            elif etype == "image":
                result["img_caption"] = elem.get("img_caption", [])
                if elem.get("img_path"):
                    result["img_path"] = elem["img_path"]

            elif etype in ("list", "list_item"):
                result["list_content"] = elem.get("list_content", elem.get("text", ""))

            else:
                # text, header, page_number, etc.
                result["text"] = elem.get("text", "")
                if elem.get("text_level") is not None:
                    result["text_level"] = elem["text_level"]

            return result

        except IndexError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 7 — get_table_image
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_table_image(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        element_index: Annotated[int, _ELEMENT_INDEX_FIELD],
    ) -> Dict[str, Any]:
        """Return the screenshot image of a table element as base64-encoded data.

        Use this to visually verify that the markdown conversion from
        ``get_element_detail`` accurately represents the original table.
        Only applicable to elements with ``type == "table"`` and a valid ``img_path``.

        Returns:
            {
                "element_index": <int>,
                "page_idx": <int>,
                "img_path": "<relative_path>",
                "media_type": "image/jpeg" | "image/png" | ...,
                "data_base64": "<base64 string>"
            }
        """
        try:
            elem = get_store_fn().get_element(report_dir, element_index)

            if elem.get("type") != "table":
                return {
                    "error": (
                        f"Element {element_index} is of type '{elem.get('type')}', "
                        "not 'table'. get_table_image only supports table elements."
                    ),
                }

            img_path_rel = elem.get("img_path")
            if not img_path_rel:
                return {
                    "error": (
                        f"Element {element_index} has no img_path. "
                        "The table image may not have been extracted during parsing."
                    ),
                }

            abs_dir = Path(report_dir).resolve()
            img_abs = abs_dir / img_path_rel
            if not img_abs.exists():
                return {"error": f"Image file not found: {img_abs}"}

            raw = img_abs.read_bytes()
            media_type, _ = mimetypes.guess_type(str(img_abs))
            if not media_type:
                media_type = "application/octet-stream"

            return {
                "element_index": element_index,
                "page_idx": elem.get("page_idx"),
                "img_path": img_path_rel,
                "media_type": media_type,
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }

        except IndexError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": str(exc)}

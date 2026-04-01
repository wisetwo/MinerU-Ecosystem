"""MCP tool registration for finreport — financial report navigation."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional

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
# Helper formatting functions
# ---------------------------------------------------------------------------

def _citation(index: int) -> str:
    """Return a citation tag for element *index*, e.g. '[Element#42]'."""
    return f"[Element#{index}]"


def _table_preview(html: str, max_bytes: int = 500) -> tuple[str, bool]:
    """Return a truncated Markdown preview of *html* and a has_more flag."""
    md = html_table_to_markdown(html)
    encoded = md.encode("utf-8")
    if len(encoded) <= max_bytes:
        return md, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "…", True


def _format_element_brief(index: int, elem: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact representation of *elem* suitable for list views.

    Tables include only a 500-byte HTML preview; other types include full text.
    """
    etype = elem.get("type", "unknown")
    base: Dict[str, Any] = {
        "element_index": index,
        "citation": _citation(index),
        "type": etype,
        "page_idx": elem.get("page_idx"),
    }

    if etype == "table":
        html = elem.get("table_body", "")
        preview, has_more = _table_preview(html)
        base["table_caption"] = elem.get("table_caption", [])
        base["table_preview"] = preview
        base["has_more"] = has_more
        if elem.get("img_path"):
            base["img_path"] = elem["img_path"]
    elif etype == "image":
        base["img_caption"] = elem.get("img_caption", [])
        if elem.get("img_path"):
            base["img_path"] = elem["img_path"]
    elif etype in ("list", "list_item"):
        base["list_items"] = elem.get("list_content", elem.get("text", ""))
    else:
        # text, header, page_number, etc.
        base["text"] = elem.get("text", "")

    return base


def _format_element_full(
    index: int, elem: Dict[str, Any], report_dir: str
) -> Dict[str, Any]:
    """Return the complete representation of *elem*, including Markdown for tables."""
    etype = elem.get("type", "unknown")
    base: Dict[str, Any] = {
        "element_index": index,
        "citation": _citation(index),
        "type": etype,
        "page_idx": elem.get("page_idx"),
        "bbox": elem.get("bbox"),
    }

    if etype == "table":
        html = elem.get("table_body", "")
        base["table_body_html"] = html
        base["table_body_markdown"] = html_table_to_markdown(html)
        base["table_caption"] = elem.get("table_caption", [])
        base["table_footnote"] = elem.get("table_footnote", [])
        if elem.get("img_path"):
            base["img_path"] = elem["img_path"]
    elif etype == "image":
        base["img_caption"] = elem.get("img_caption", [])
        if elem.get("img_path"):
            base["img_path"] = elem["img_path"]
    elif etype in ("list", "list_item"):
        base["list_content"] = elem.get("list_content", elem.get("text", ""))
    else:
        base["text"] = elem.get("text", "")

    return base


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP, get_store_fn: Callable[[], DocumentStore]) -> None:
    """Register all 6 finreport tools onto *mcp*, injecting *get_store_fn*.

    *get_store_fn* is called on every tool invocation so that the store
    instance can be replaced (e.g. with a different cache size) after
    module initialisation without re-registering tools.
    """

    # ------------------------------------------------------------------
    # Tool 1 — get_document_info
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_document_info(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
    ) -> Dict[str, Any]:
        """Return basic metadata about a MinerU-parsed financial report.

        Call this FIRST when starting to analyse a report to understand its
        structure before diving into specific pages or elements.

        Returns:
            success: {
                "status": "success",
                "report_dir": "<abs_path>",
                "total_elements": <int>,
                "total_pages": <int>,
                "element_type_counts": {"text": ..., "table": ..., ...},
                "has_images": <bool>,
                "md_files": ["<filename>.md", ...]
            }
            error: {"status": "error", "error": "<message>"}
        """
        try:
            content = get_store_fn().get(report_dir)
            abs_dir = str(Path(report_dir).resolve())

            total_pages = max((e.get("page_idx", 0) for e in content), default=0) + 1

            type_counts: Dict[str, int] = {}
            for elem in content:
                t = elem.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1

            has_images = any(
                elem.get("type") == "image" or elem.get("img_path")
                for elem in content
            )

            md_files = [p.name for p in Path(abs_dir).glob("*.md")]

            return {
                "status": "success",
                "report_dir": abs_dir,
                "total_elements": len(content),
                "total_pages": total_pages,
                "element_type_counts": type_counts,
                "has_images": has_images,
                "md_files": sorted(md_files),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 2 — get_outline
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_outline(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
    ) -> Dict[str, Any]:
        """Generate a hierarchical outline from heading elements in the report.

        Extracts elements that have a ``text_level`` field (H1/H2/H3) and
        returns them as an ordered outline list with element indices so the
        user can jump to any section.

        Returns:
            success: {
                "status": "success",
                "total_headings": <int>,
                "outline": [
                    {
                        "element_index": <int>,
                        "citation": "[Element#<n>]",
                        "level": <1|2|3>,
                        "text": "<heading text>",
                        "page_idx": <int>
                    },
                    ...
                ]
            }
            error: {"status": "error", "error": "<message>"}
        """
        try:
            content = get_store_fn().get(report_dir)
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
                    "citation": _citation(i),
                    "level": level_int,
                    "text": text,
                    "page_idx": elem.get("page_idx"),
                })
            return {
                "status": "success",
                "total_headings": len(outline),
                "outline": outline,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 3 — get_page_content
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_page_content(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        page_idx: Annotated[int, _PAGE_IDX_FIELD],
    ) -> Dict[str, Any]:
        """Return all elements on a given page of the report.

        Tables are returned with a 500-byte Markdown preview (``table_preview``);
        use ``get_element_detail`` to retrieve the full table content.

        Returns:
            success: {
                "status": "success",
                "page_idx": <int>,
                "total_pages": <int>,
                "element_count": <int>,
                "elements": [<element_brief>, ...]
            }
            error: {"status": "error", "error": "<message>"}
        """
        try:
            elements, total_pages = get_store_fn().get_page_elements(report_dir, page_idx)
            brief_list = [_format_element_brief(i, elem) for i, elem in elements]
            return {
                "status": "success",
                "page_idx": page_idx,
                "total_pages": total_pages,
                "element_count": len(brief_list),
                "elements": brief_list,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 4 — search_text
    # ------------------------------------------------------------------

    @mcp.tool()
    async def search_text(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        query: Annotated[str, _QUERY_FIELD],
        page_start: Annotated[Optional[int], _PAGE_START_FIELD] = None,
        page_end: Annotated[Optional[int], _PAGE_END_FIELD] = None,
    ) -> Dict[str, Any]:
        """Search for a keyword across the full document (case-insensitive).

        Searches within: ``text``, ``table_caption``, ``table_footnote``,
        ``img_caption``, ``list_content``, and raw ``table_body`` HTML.

        Returns:
            success: {
                "status": "success",
                "query": "<query>",
                "page_range": {"start": <int|null>, "end": <int|null>},
                "total_matches": <int>,
                "matches": [<element_brief>, ...]
            }
            error: {"status": "error", "error": "<message>"}
        """
        try:
            content = get_store_fn().get(report_dir)
            needle = query.lower()
            matches: List[Dict[str, Any]] = []

            for i, elem in enumerate(content):
                pg = elem.get("page_idx", 0)
                if page_start is not None and pg < page_start:
                    continue
                if page_end is not None and pg > page_end:
                    continue

                hit = False
                # text / header / page_number
                if needle in elem.get("text", "").lower():
                    hit = True
                # table fields
                if not hit:
                    for caption in elem.get("table_caption", []):
                        if needle in caption.lower():
                            hit = True
                            break
                if not hit:
                    for fn in elem.get("table_footnote", []):
                        if needle in fn.lower():
                            hit = True
                            break
                if not hit and needle in elem.get("table_body", "").lower():
                    hit = True
                # image caption
                if not hit:
                    for cap in elem.get("img_caption", []):
                        if needle in cap.lower():
                            hit = True
                            break
                # list content
                if not hit and needle in elem.get("list_content", "").lower():
                    hit = True

                if hit:
                    matches.append(_format_element_brief(i, elem))

            return {
                "status": "success",
                "query": query,
                "page_range": {"start": page_start, "end": page_end},
                "total_matches": len(matches),
                "matches": matches,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 5 — get_element_detail
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_element_detail(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        element_index: Annotated[int, _ELEMENT_INDEX_FIELD],
    ) -> Dict[str, Any]:
        """Return the full content of a single element by its index.

        For tables, both the raw HTML (``table_body_html``) and the converted
        GFM Markdown (``table_body_markdown``) are included.

        Returns:
            success: {
                "status": "success",
                "element_index": <int>,
                "citation": "[Element#<n>]",
                "type": "<type>",
                "page_idx": <int>,
                "bbox": [...],
                ... (type-specific fields)
            }
            error: {"status": "error", "error": "<message>"}
        """
        try:
            elem = get_store_fn().get_element(report_dir, element_index)
            result = _format_element_full(element_index, elem, report_dir)
            result["status"] = "success"
            return result
        except IndexError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Tool 6 — get_table_image
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_table_image(
        report_dir: Annotated[str, _REPORT_DIR_FIELD],
        element_index: Annotated[int, _ELEMENT_INDEX_FIELD],
    ) -> Dict[str, Any]:
        """Return the screenshot image of a table element as base64-encoded data.

        Use this to visually verify that the Markdown conversion (from
        ``get_element_detail``) accurately represents the original table.
        Only applicable to elements with ``type == "table"`` and a valid
        ``img_path``.

        Returns:
            success: {
                "status": "success",
                "element_index": <int>,
                "citation": "[Element#<n>]",
                "page_idx": <int>,
                "img_path": "<relative_path>",
                "img_abs_path": "<absolute_path>",
                "media_type": "image/jpeg" | "image/png" | ...,
                "data_base64": "<base64 string>"
            }
            error: {"status": "error", "error": "<message>"}
        """
        try:
            elem = get_store_fn().get_element(report_dir, element_index)

            if elem.get("type") != "table":
                return {
                    "status": "error",
                    "error": (
                        f"Element {element_index} is of type '{elem.get('type')}', "
                        "not 'table'. get_table_image only supports table elements."
                    ),
                }

            img_path_rel = elem.get("img_path")
            if not img_path_rel:
                return {
                    "status": "error",
                    "error": (
                        f"Element {element_index} has no img_path. "
                        "The table image may not have been extracted during parsing."
                    ),
                }

            abs_dir = Path(report_dir).resolve()
            img_abs = abs_dir / img_path_rel
            if not img_abs.exists():
                return {
                    "status": "error",
                    "error": f"Image file not found: {img_abs}",
                }

            raw = img_abs.read_bytes()
            media_type, _ = mimetypes.guess_type(str(img_abs))
            if not media_type:
                media_type = "application/octet-stream"

            return {
                "status": "success",
                "element_index": element_index,
                "citation": _citation(element_index),
                "page_idx": elem.get("page_idx"),
                "img_path": img_path_rel,
                "img_abs_path": str(img_abs),
                "media_type": media_type,
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }
        except IndexError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

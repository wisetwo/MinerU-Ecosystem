#!/usr/bin/env python3
# Requires Python 3.11+ (uses X | Y union type annotation syntax)
# -*- coding: utf-8 -*-
"""
add_md_to_server_v2.py
======================
Splits an annual report into per-chapter Markdown files using content_list.json
(rather than splitting the overall Markdown file).

Key differences from v1:
1. Data source: rebuilds Markdown item-by-item from content_list.json instead of
   regex-splitting the whole .md file.
2. TOC detection: first locates "TOC pages" in content_list (pages whose text
   contains CONTENTS / 目录 keywords); if the text items can be parsed directly
   into a chapter list that path is taken; otherwise pdftoppm renders the page as
   a JPEG which is then sent to a vision model for recognition.
3. Chapter splitting: uses text_level=1 headings in content_list as boundaries,
   aggregates items by page_idx, then concatenates.

Usage:
    python3 add_md_to_server_v2.py

Environment variables (vision model — set any one):
    OPENAI_API_KEY        + OPENAI_BASE_URL (optional, default https://api.openai.com/v1)
    ANTHROPIC_AUTH_TOKEN  + ANTHROPIC_BASE_URL + ANTHROPIC_CUSTOM_HEADERS (optional)
    OPENROUTER_API_KEY

    VISION_MODEL          vision model name, default gpt-4o
"""

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Auto-load .env from the same directory or any ancestor (searches upward up to 4 levels)
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        cur = Path(__file__).resolve().parent
        for _ in range(4):
            env_file = cur / ".env"
            if env_file.exists():
                load_dotenv(env_file, override=False)
                break
            cur = cur.parent
    except ImportError:
        pass  # python-dotenv not installed — silently skip

_load_dotenv()

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------
TESTS_DIR  = Path(__file__).parent
# DOC_NAME   = "小米集团-1810-2024年年报-demo"
DOC_NAME   = "拼多多-PDD-2024年年报-demo"
SOURCE_DIR = TESTS_DIR / "pdf-demo-output" / DOC_NAME
PDF_PATH   = TESTS_DIR / "pdf-demo" / f"{DOC_NAME}.pdf"
SERVER_DIR = TESTS_DIR / "server" / DOC_NAME

# Vision model cache file (avoids redundant API calls)
LLM_CACHE_FILE = SOURCE_DIR / f"{DOC_NAME}__toc_by_vision.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# 1. content_list.json utilities
# ===========================================================================

def load_content_list(source_dir: Path) -> list[dict]:
    path = source_dir / "content_list.json"
    if not path.exists():
        raise FileNotFoundError(f"content_list.json not found: {path}")
    items = json.loads(path.read_text(encoding="utf-8"))
    # Stamp each item with its original array index so downstream renderers can
    # emit <!-- index: N --> comments for traceability.
    for i, item in enumerate(items):
        item["_index"] = i
    return items


def find_toc_pages(items: list[dict]) -> list[int]:
    """
    Locate the page_idx list of TOC pages within content_list.

    Two detection strategies are applied:

    1. **type=text** with TOC keyword — classic HK / Chinese annual report style
       (e.g. a level-1 heading whose text is "CONTENTS" or "目录").

    2. **type=header** with TOC keyword AND the same page also contains a
       ``type=table`` item — US-listed report (20-F) style where "Table of
       Contents" is a running page-header printed on *every* page, but the
       actual TOC page is distinguished by having its chapter list rendered as
       a table by MinerU.  Only the *first contiguous run* of such pages is
       kept; later table pages (e.g. financial statement tables) are excluded.
    """
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)

    # Collect pages that contain at least one table item (needed for strategy 2)
    pages_with_table: set[int] = {
        item["page_idx"] for item in items if item.get("type") == "table"
    }

    strategy1_pages: list[int] = []
    strategy2_candidates: list[int] = []

    for item in items:
        item_type = item.get("type")
        text = (item.get("text") or "").strip()
        if not toc_kw.search(text):
            continue
        if item_type == "text":
            # Strategy 1: explicit TOC heading rendered as a text item
            strategy1_pages.append(item["page_idx"])
        elif item_type == "header" and item["page_idx"] in pages_with_table:
            # Strategy 2 candidate: running header on a page that also has a table
            strategy2_candidates.append(item["page_idx"])

    if strategy1_pages:
        return sorted(set(strategy1_pages))

    # Strategy 2: keep only the first contiguous run of candidate pages so that
    # later financial-statement table pages are not mis-classified as TOC pages.
    candidates = sorted(set(strategy2_candidates))
    if not candidates:
        return []
    toc_run: list[int] = [candidates[0]]
    for pg in candidates[1:]:
        if pg == toc_run[-1] + 1:
            toc_run.append(pg)
        else:
            break  # stop at first gap
    return toc_run


def items_on_pages(items: list[dict], page_idxs: list[int]) -> list[dict]:
    """Return all items whose page_idx falls within the given set (0-based)."""
    page_set = set(page_idxs)
    return [i for i in items if i.get("page_idx") in page_set]


# ===========================================================================
# 2. Parse TOC directly from content_list (fast path)
# ===========================================================================

# TOC line format: "Chapter Title  123" or "Chapter Title 123 "
_TOC_LINE_RE = re.compile(
    r'^(?P<title>.+?)\s+(?P<page>\d+)\s*$'
)


def parse_toc_from_text_items(toc_items: list[dict]) -> list[dict]:
    """
    Try to extract chapter information directly from TOC-page text items.
    Each item.text typically looks like "CHAPTER TITLE  12 ".

    Returns [{"title": "...", "page": int}, ...].
    Returns an empty list (triggering the vision-model fallback) when:
    - no entries were parsed, or
    - valid matches cover less than 50% of non-empty text lines
      (indicating that page numbers were rendered as images).
    """
    results: list[dict] = []
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)

    non_toc_header_texts: list[str] = []  # used to count total valid text lines

    for item in toc_items:
        if item.get("type") != "text":
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        # Skip the TOC heading line itself
        if toc_kw.search(text):
            continue
        non_toc_header_texts.append(text)
        m = _TOC_LINE_RE.match(text)
        if m:
            results.append({
                "title": m.group("title").strip(),
                "page":  int(m.group("page")),
            })

    # Quality check: if match rate < 50%, many page numbers were rendered as images
    total = len(non_toc_header_texts)
    if total > 0 and len(results) < total * 0.5:
        logger.info(
            f"Fast path matched only {len(results)}/{total} TOC entries"
            " (some page numbers may be rendered as images) — falling back to vision model"
        )
        return []

    return results


# ===========================================================================
# 3. Vision model recognition (slow path)
# ===========================================================================

def render_pdf_pages_to_jpeg(
    pdf_path: Path,
    page_idxs: list[int],          # 0-based
    dpi: int = 150,
    keep_dir: Path | None = None,  # if not None, screenshots are saved here
) -> list[Path]:
    """
    Render specified PDF pages (0-based) to JPEG using pdftoppm.
    Returns a list of JPEG file paths.

    If keep_dir is provided the images are written there (caller manages lifetime);
    otherwise a temporary directory is used (caller is responsible for cleanup).
    """
    out_dir = keep_dir if keep_dir is not None else Path(tempfile.mkdtemp(prefix="toc_render_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_prefix = str(out_dir / "page")
    jpeg_paths: list[Path] = []

    for page_idx in page_idxs:
        page_1based = page_idx + 1
        subprocess.run(
            [
                "pdftoppm",
                "-f", str(page_1based),
                "-l", str(page_1based),
                "-r", str(dpi),
                "-jpeg",
                str(pdf_path),
                out_prefix,
            ],
            check=True,
            capture_output=True,
        )
        candidates = sorted(out_dir.glob("*.jpg")) + sorted(out_dir.glob("*.jpeg"))
        if candidates:
            jpeg_paths.append(candidates[-1])

    return jpeg_paths


def _build_vision_client():
    """
    Build an OpenAI-compatible client from environment variables.
    Priority: OPENAI_API_KEY > ANTHROPIC_AUTH_TOKEN > OPENROUTER_API_KEY
    Returns (client, model_name).
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package is required: pip install openai")

    model = os.environ.get("VISION_MODEL", "gpt-4o")

    if key := os.environ.get("OPENAI_API_KEY"):
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        client = OpenAI(api_key=key, base_url=base_url)
        logger.debug(f"Using OpenAI-compatible endpoint: {base_url}, model: {model}")
        return client, model

    if key := os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        # Parse custom headers (format: "Key: Value\nKey2: Value2")
        extra_headers: dict[str, str] = {}
        for line in os.environ.get("ANTHROPIC_CUSTOM_HEADERS", "").split("\n"):
            if ": " in line:
                k, v = line.split(": ", 1)
                extra_headers[k.strip()] = v.strip()
        client = OpenAI(
            api_key=key,
            base_url=base_url.rstrip("/") + "/v1",
            default_headers=extra_headers,
        )
        model = os.environ.get("VISION_MODEL") or os.environ.get(
            "ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-5"
        )
        logger.debug(f"Using Anthropic endpoint: {base_url}, model: {model}")
        return client, model

    if key := os.environ.get("OPENROUTER_API_KEY"):
        client = OpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
        )
        model = os.environ.get("VISION_MODEL", "openai/gpt-4o")
        logger.debug(f"Using OpenRouter, model: {model}")
        return client, model

    raise RuntimeError(
        "No API key found. Set OPENAI_API_KEY / ANTHROPIC_AUTH_TOKEN / OPENROUTER_API_KEY"
    )


_VISION_PROMPT = """\
This image is a table of contents (TOC) page from a corporate annual report.
Please extract ALL chapter/section titles along with their corresponding page numbers.

Rules:
- Include every line that has a title and a page number.
- Do NOT include group headers that have no page number (e.g. "Part I", "Part II").
- Preserve the exact title text as it appears (including uppercase, punctuation).
- Return ONLY a JSON array, no explanation, no markdown fences.

Format: [{"title": "CHAPTER TITLE", "page": 12}, ...]
"""


def call_vision_model_for_toc(jpeg_paths: list[Path]) -> list[dict]:
    """
    Send one or more TOC screenshots to the vision model and return a chapter list.
    Structure: [{"title": "...", "page": int}, ...]
    """
    client, model = _build_vision_client()

    content: list[dict] = []
    for jpeg_path in jpeg_paths:
        img_b64 = base64.standard_b64encode(jpeg_path.read_bytes()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
        })
    content.append({"type": "text", "text": _VISION_PROMPT})

    logger.info(f"Calling vision model [{model}] on {len(jpeg_paths)} TOC screenshot(s)…")
    response = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()
    logger.debug(f"Vision model raw response: {raw[:300]}")

    # Strip possible markdown code fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE)

    parsed = json.loads(raw.strip())
    if not isinstance(parsed, list):
        raise ValueError(f"Vision model response is not a list: {raw[:200]}")

    # Normalise fields
    results: list[dict] = []
    for item in parsed:
        title = str(item.get("title", "")).strip()
        page  = item.get("page")
        if title and page is not None:
            results.append({"title": title, "page": int(page)})

    logger.info(f"Vision model recognised {len(results)} chapter(s)")
    return results


# ===========================================================================
# 4. TOC retrieval (with cache)
# ===========================================================================

def get_toc(
    items: list[dict],
    pdf_path: Path,
    source_dir: Path,
    force_vision: bool = False,
    keep_temp: bool = False,
) -> tuple[list[dict], bool]:
    """
    Retrieve the chapter list from the TOC.

    Returns (chapters, used_vision):
    - chapters    : [{"title": str, "page": int}, ...]
    - used_vision : True if the result came from the vision model (or its cache),
                    False if it came from the text fast path.
    """
    cache_file = LLM_CACHE_FILE

    # Cache hit (from a previous vision model call)
    if cache_file.exists() and not force_vision:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(cached, list) and cached:
            logger.info(f"Using TOC cache: {cache_file.name} ({len(cached)} chapters)")
            return cached, True  # cache originates from vision model

    # Locate TOC pages
    toc_pages = find_toc_pages(items)
    if not toc_pages:
        raise ValueError("No TOC page found in content_list (expected CONTENTS / 目录 keyword)")
    logger.info(f"Detected TOC page_idx(s): {toc_pages}")

    # Fast path: parse chapters directly from text items
    if not force_vision:
        toc_items = items_on_pages(items, toc_pages)
        chapters = parse_toc_from_text_items(toc_items)
        if chapters:
            logger.info(f"Parsed {len(chapters)} chapters from content_list text (fast path)")
            _save_toc_cache(cache_file, chapters)
            return chapters, False  # text-parsed, not vision
        logger.info("Text parsing yielded 0 chapters — switching to vision model")

    # Slow path: vision model
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"Original PDF needed to render TOC screenshots but not found: {pdf_path}\n"
            "Place the PDF under tests/pdf-demo/, or ensure content_list is parseable "
            "and set force_vision=False."
        )

    keep_dir = (source_dir / "debug_toc_render") if keep_temp else None
    if keep_dir:
        keep_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"--keep-temp enabled; screenshots will be saved to: {keep_dir}")

    jpeg_paths = render_pdf_pages_to_jpeg(pdf_path, toc_pages, dpi=150, keep_dir=keep_dir)
    logger.info(f"Rendered {len(jpeg_paths)} screenshot(s): {[p.name for p in jpeg_paths]}")

    try:
        chapters = call_vision_model_for_toc(jpeg_paths)
    finally:
        if not keep_temp:
            for p in jpeg_paths:
                try:
                    p.unlink(missing_ok=True)
                    if not list(p.parent.iterdir()):
                        p.parent.rmdir()
                except Exception:
                    pass

    if not chapters:
        raise ValueError("Vision model returned no chapters — check TOC screenshots or model config")

    _save_toc_cache(cache_file, chapters)
    return chapters, True  # vision model path


def _save_toc_cache(cache_file: Path, chapters: list[dict]) -> None:
    cache_file.write_text(
        json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"TOC cached to {cache_file.name}")


# ===========================================================================
# 5. content_list → Markdown reconstruction
# ===========================================================================

def prettify_html_tables(content: str) -> str:
    """Expand single-line HTML tables into indented multi-line format (consistent with v1)."""

    def _prettify_one(m: re.Match) -> str:
        raw = m.group(0)
        spaced = re.sub(r">\s*<", ">\n<", raw)
        lines = spaced.split("\n")
        result: list[str] = []
        indent = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if re.match(r"^</(table|thead|tbody|tfoot|tr|th|td)>", line, re.IGNORECASE):
                if result:
                    prev = result[-1].strip()
                    if re.match(r"^<(td|th)(\s[^>]*)?>$", prev, re.IGNORECASE):
                        result[-1] = result[-1] + line
                        indent -= 1
                        continue
                indent -= 1
            result.append("  " * indent + line)
            if re.match(r"^<(table|thead|tbody|tfoot|tr|th|td)(\s[^>]*)?>(?!.*</\1>)", line, re.IGNORECASE):
                indent += 1

        return "\n" + "\n".join(result) + "\n"

    return re.sub(r"<table>.*?</table>", _prettify_one, content, flags=re.DOTALL | re.IGNORECASE)


def item_to_markdown(item: dict, images_rel_prefix: str = "images") -> str:
    """Convert a single content_list item to a Markdown string.

    When the item carries an ``_index`` field (stamped by ``load_content_list``),
    a ``<!-- index: N -->`` comment is prepended to the rendered content so that
    every block in the output can be traced back to its position in the original
    content_list.json array.  The comment is omitted when the item produces no
    visible output (e.g. skipped headers / page numbers).
    """
    t = item.get("type", "")

    if t in ("header"):
        # Running page headers/footers add no content value
        return ""

    if t == "page_number":
        text = (item.get("text") or "").strip()
        body = f"\n<!-- page: {text} -->\n" if text else ""
    elif t == "text":
        text  = (item.get("text") or "").rstrip()
        level = item.get("text_level")
        body  = (f"# {text}\n" if level == 1 else f"{text}\n") if text else ""
    elif t == "page_footnote":
        text = (item.get("text") or "").strip()
        body = f"\n---\n*{text}*\n" if text else ""
    elif t == "image":
        img_path = item.get("img_path", "")
        captions = item.get("image_caption") or []
        caption_text = " ".join(captions).strip()
        alt = caption_text or "image"
        body = f"\n![{alt}]({img_path})\n"
    elif t == "table":
        parts: list[str] = []
        captions = item.get("table_caption") or []
        if captions:
            parts.append("\n**" + " ".join(captions).strip() + "**\n")
        table_body = item.get("table_body", "")
        if table_body:
            parts.append(prettify_html_tables(table_body))
        footnotes = item.get("table_footnote") or []
        for fn in footnotes:
            if fn.strip():
                parts.append(f"*{fn.strip()}*\n")
        body = "\n".join(parts) + "\n"
    elif t == "list":
        lines: list[str] = []
        for li in item.get("list_items") or []:
            li = li.strip()
            if li:
                lines.append(f"- {li}")
        body = "\n".join(lines) + "\n" if lines else ""
    else:
        # Unknown type — degrade to plain text
        raw = item.get("text", "")
        body = f"{raw}\n" if raw else ""

    if not body:
        return ""

    # Prepend the original content_list array index as an HTML comment so that
    # every rendered block can be traced back to its source item.
    # page_number items are structural markers only — no id comment needed.
    if t == "page_number":
        return body
    idx = item.get("_index")
    prefix = f"<!-- id: {idx} -->\n" if idx is not None else ""
    return prefix + body


def build_markdown_from_items(items: list[dict]) -> str:
    """Concatenate a list of content_list items into a complete Markdown string."""
    parts: list[str] = []
    for item in items:
        md = item_to_markdown(item)
        if md:
            parts.append(md)
    return "\n".join(parts)


# ===========================================================================
# 6. Chapter splitting
# ===========================================================================

def chapter_to_filename(title: str) -> str:
    """Convert a chapter title to a valid filename."""
    clean = re.sub(r"[^\w\s\-.]", "", title)
    clean = re.sub(r"\s+", "_", clean.strip()).lower()
    return f"{clean}.md"


def copy_images(source_dir: Path, server_dir: Path) -> None:
    """Copy (or merge) source_dir/images into server_dir/images."""
    src = source_dir / "images"
    if not src.is_dir():
        logger.debug("No images subdirectory found — skipping")
        return
    dst = server_dir / "images"
    if dst.exists():
        for item in src.iterdir():
            if item.is_file():
                dst_f = dst / item.name
                if not dst_f.exists():
                    shutil.copy2(item, dst_f)
    else:
        shutil.copytree(src, dst)
        logger.info(f"Copied images → {dst}")


def split_items_by_chapters(
    all_items: list[dict],
    toc: list[dict],
) -> dict[str, list[dict]]:
    """
    Group content_list items by TOC chapter.

    Matching strategy (tried in priority order):
    1. A single text_level=1 item whose text exactly matches the chapter title
       (ignoring leading/trailing whitespace and case).
    2. Several consecutive text_level=1 items on the same page whose concatenated
       text matches the chapter title (handles titles split across lines, e.g.
       "FIVE-YEAR " + "FINANCIAL SUMMARY ").

    The returned dict always contains these keys:
    - "pre_toc"  : items before the TOC page (cover, shareholder notices, etc.)
    - "toc_page" : items from the TOC page itself (kept for reference, not written)
    - <chapter title> : items belonging to that chapter
    """

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip()).lower()

    toc_titles = [entry["title"] for entry in toc]
    toc_norm   = {_norm(t): t for t in toc_titles}

    # Identify TOC page_idx values using the same two-strategy logic as
    # find_toc_pages(): type=text match for HK/Chinese reports; type=header
    # match only when the page also contains a table, keeping only the first
    # contiguous run to avoid mis-classifying financial-statement table pages.
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)
    pages_with_table: set[int] = {
        item["page_idx"] for item in all_items if item.get("type") == "table"
    }
    _s1_pages: list[int] = []
    _s2_candidates: list[int] = []
    for item in all_items:
        item_type = item.get("type")
        text = (item.get("text") or "").strip()
        if not toc_kw.search(text):
            continue
        if item_type == "text":
            _s1_pages.append(item["page_idx"])
        elif item_type == "header" and item["page_idx"] in pages_with_table:
            _s2_candidates.append(item["page_idx"])
    if _s1_pages:
        toc_page_idxs: set[int] = set(_s1_pages)
    else:
        _s2_sorted = sorted(set(_s2_candidates))
        _toc_run: list[int] = [_s2_sorted[0]] if _s2_sorted else []
        for _pg in _s2_sorted[1:]:
            if _pg == _toc_run[-1] + 1:
                _toc_run.append(_pg)
            else:
                break
        toc_page_idxs = set(_toc_run)

    # Pre-process: collect all text_level=1 item positions (excluding TOC pages)
    heading_items: list[tuple[int, dict]] = [
        (idx, item)
        for idx, item in enumerate(all_items)
        if item.get("type") == "text"
        and item.get("text_level") == 1
        and item.get("page_idx") not in toc_page_idxs
    ]

    # Group by page and attempt concatenation matching
    chapter_starts: list[tuple[int, str]] = []
    already_matched: set[str] = set()

    page_groups: dict[int, list[tuple[int, dict]]] = {}
    for item_idx, item in heading_items:
        pg = item.get("page_idx", -1)
        page_groups.setdefault(pg, []).append((item_idx, item))

    for pg in sorted(page_groups):
        group = page_groups[pg]
        n = len(group)
        for start in range(n):
            accumulated = ""
            for end in range(start, n):
                text = (group[end][1].get("text") or "").strip()
                accumulated = (accumulated + " " + text).strip() if accumulated else text
                norm_acc = _norm(accumulated)
                if norm_acc in toc_norm:
                    canonical = toc_norm[norm_acc]
                    if canonical not in already_matched:
                        chapter_starts.append((group[start][0], canonical))
                        already_matched.add(canonical)
                    break

    chapter_starts.sort(key=lambda x: x[0])

    result: dict[str, list[dict]] = {}

    # First chapter item position (fall back to end if no match)
    first_chapter_idx = chapter_starts[0][0] if chapter_starts else len(all_items)

    # pre_toc: items before the first chapter (excluding TOC pages)
    result["pre_toc"] = [
        item for item in all_items[:first_chapter_idx]
        if item.get("page_idx") not in toc_page_idxs
    ]

    # toc_page: raw items from the TOC page (kept for reference, not written directly)
    result["toc_page"] = [
        item for item in all_items
        if item.get("page_idx") in toc_page_idxs
    ]

    # Chapter items
    for i, (start_idx, title) in enumerate(chapter_starts):
        end_idx = chapter_starts[i + 1][0] if i + 1 < len(chapter_starts) else len(all_items)
        result[title] = all_items[start_idx:end_idx]

    missed = [t for t in toc_titles if t not in already_matched]
    if missed:
        logger.warning(f"{len(missed)} chapter(s) not matched in content_list "
                       f"(may be outside the demo page range):")
        for t in missed:
            logger.warning(f"  - {t!r}")

    return result


# ===========================================================================
# 7. Main pipeline
# ===========================================================================

def build_toc_md(toc: list[dict], toc_page_items: list[dict], used_vision: bool) -> str:
    """
    Generate the content of toc.md.

    Two scenarios:
    - used_vision=True (or fast path covered all entries):
        Generate a Markdown table directly from the toc list, with relative links
        for each chapter title.
    - Fast path (used_vision=False):
        First render the raw text from the TOC page in content_list (preserving
        original layout), then append a full navigation table with links below.
        Chapter names found inline are replaced with links.

    toc_page_items: the "toc_page" segment returned by split_items_by_chapters.
    """
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)

    # Build the link table (output in both scenarios)
    link_rows: list[str] = [
        "| Chapter | Page |",
        "| --- | --- |",
    ]
    for entry in toc:
        filename = chapter_to_filename(entry["title"])
        link_rows.append(f"| [{entry['title']}](./{filename}) | {entry['page']} |")
    link_table = "\n".join(link_rows)

    if used_vision:
        # Vision model path: output only the clean link table (raw TOC text is unreliable)
        return f"# Table of Contents\n\n{link_table}\n"

    # Fast path: render original TOC page text (with inline chapter links),
    # then append the full navigation table
    title_to_file = {e["title"]: chapter_to_filename(e["title"]) for e in toc}

    original_lines: list[str] = []
    for item in toc_page_items:
        if item.get("type") != "text":
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        if toc_kw.search(text):
            # TOC heading line itself — render as H1
            original_lines.append(f"# {text}\n")
            continue
        # Replace inline chapter names with links ("Title  page_num" format)
        replaced = text
        for title, fname in title_to_file.items():
            if title in replaced:
                replaced = replaced.replace(title, f"[{title}](./{fname})")
                break
        original_lines.append(replaced)

    original_block = "\n".join(original_lines)

    return (
        f"{original_block}\n\n"
        f"---\n\n"
        f"## Navigation\n\n"
        f"{link_table}\n"
    )


def add_md_to_server_v2(
    source_dir: Path = SOURCE_DIR,
    pdf_path: Path   = PDF_PATH,
    server_dir: Path = SERVER_DIR,
    force_vision: bool = False,
    keep_temp: bool = False,
) -> None:
    """
    Split an annual report into per-chapter files under server_dir, driven by
    content_list.json.

    Output files:
    - server_dir/index.md    front matter (content before TOC page, excluding TOC)
    - server_dir/toc.md      navigation TOC (chapter link table; fast path also
                              preserves original TOC layout)
    - server_dir/<ch>.md     body of each chapter
    - server_dir/images/     image assets

    Args:
        force_vision: skip text fast path and always use the vision model.
        keep_temp:    keep rendered PDF screenshots in source_dir/debug_toc_render/
                      for debugging.
    """
    logger.info(f"Source dir : {source_dir}")
    logger.info(f"PDF        : {pdf_path}")
    logger.info(f"Output dir : {server_dir}")

    # 1. Load content_list
    items = load_content_list(source_dir)
    logger.info(f"Loaded content_list with {len(items)} item(s)")

    # 2. Retrieve TOC
    toc, used_vision = get_toc(items, pdf_path, source_dir, force_vision=force_vision, keep_temp=keep_temp)

    logger.info(
        f"TOC chapters ({len(toc)} total, source: "
        f"{'vision model / cache' if used_vision else 'text parsing'}):"
    )
    for entry in toc:
        logger.info(f"  p.{entry['page']:>4}  {entry['title']}")

    # 3. Create output directory
    server_dir.mkdir(parents=True, exist_ok=True)

    # 4. Group items by chapter
    sections = split_items_by_chapters(items, toc)
    matched_count = sum(1 for k in sections if k not in ("pre_toc", "toc_page"))
    logger.info(f"Successfully matched {matched_count} chapter(s)")

    # 5. Write index.md (front matter: content before the TOC page)
    pre_toc_items = sections.get("pre_toc", [])
    index_md = build_markdown_from_items(pre_toc_items).strip()
    index_path = server_dir / "index.md"
    index_path.write_text(index_md + "\n", encoding="utf-8")
    logger.info(f"Wrote index.md ({len(index_md)} bytes, {len(pre_toc_items)} items)")

    # 6. Write toc.md (navigation TOC with links)
    toc_page_items = sections.get("toc_page", [])
    toc_md = build_toc_md(toc, toc_page_items, used_vision=used_vision)
    toc_path = server_dir / "toc.md"
    toc_path.write_text(toc_md, encoding="utf-8")
    logger.info(f"Wrote toc.md ({'vision model link table' if used_vision else 'raw text + link table'})")

    # 7. Write per-chapter .md files
    saved = 0
    for entry in toc:
        title    = entry["title"]
        filename = chapter_to_filename(title)
        chapter_items = sections.get(title)

        if not chapter_items:
            logger.warning(f"  [skip] no content for: {title!r}")
            continue

        chapter_md = build_markdown_from_items(chapter_items)
        out_path = server_dir / filename
        out_path.write_text(chapter_md.strip() + "\n", encoding="utf-8")
        logger.info(f"  Wrote {filename} ({len(chapter_md)} bytes, {len(chapter_items)} items)")
        saved += 1

    logger.info(f"Chapters written: {saved} / {len(toc)}")

    # 8. Copy images
    copy_images(source_dir, server_dir)

    logger.info("✓ Done")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="add_md_to_server_v2: split an annual report using content_list.json"
    )
    parser.add_argument("--doc",          default=DOC_NAME,    help="document name (subdirectory under pdf-demo-output)")
    parser.add_argument("--force-vision", action="store_true", help="skip text fast path and force vision model")
    parser.add_argument("--keep-temp",    action="store_true", help="keep rendered PDF screenshots in source_dir/debug_toc_render/")
    parser.add_argument("--vision-model", default=None,        help="override VISION_MODEL env variable")
    args = parser.parse_args()

    if args.vision_model:
        os.environ["VISION_MODEL"] = args.vision_model

    if args.doc != DOC_NAME:
        _src = TESTS_DIR / "pdf-demo-output" / args.doc
        _pdf = TESTS_DIR / "pdf-demo" / f"{args.doc}.pdf"
        _srv = TESTS_DIR / "server" / args.doc
        LLM_CACHE_FILE = _src / f"{args.doc}__toc_by_vision.json"
        add_md_to_server_v2(
            source_dir=_src,
            pdf_path=_pdf,
            server_dir=_srv,
            force_vision=args.force_vision,
            keep_temp=args.keep_temp,
        )
    else:
        add_md_to_server_v2(force_vision=args.force_vision, keep_temp=args.keep_temp)

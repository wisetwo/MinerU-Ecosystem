#!/usr/bin/env python3
# 需要 Python 3.11+（使用了 X | Y 联合类型注解语法）
# -*- coding: utf-8 -*-
"""
add_md_to_server_v2.py
======================
基于 content_list.json（而非整体 Markdown）把年报拆分为按章节独立的 Markdown 文件。

与 v1 的核心区别：
1. 数据源：使用 content_list.json，逐条 item 重建 Markdown，而非正则切割整体 .md
2. 目录识别：先从 content_list 定位"目录页"（含 CONTENTS/目录 关键词的页面）；
   如果 content_list 里的文本项能直接解析出章节列表就直接用；
   否则用 pdftoppm 把该页渲染成 JPEG，再调用视觉大模型识别。
3. 章节切割：以 content_list 中的 text_level=1 标题为分界，按 page_idx 聚合 item 后拼接。

用法：
    python3 add_md_to_server_v2.py

环境变量（视觉模型调用，三选一即可）：
    OPENAI_API_KEY        + OPENAI_BASE_URL（可选，默认 https://api.openai.com/v1）
    ANTHROPIC_AUTH_TOKEN  + ANTHROPIC_BASE_URL + ANTHROPIC_CUSTOM_HEADERS（可选）
    OPENROUTER_API_KEY

    VISION_MODEL          指定视觉模型名称，默认 gpt-4o
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

# 自动加载同级或上级目录的 .env（优先级：当前文件的父目录逐级向上查找）
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
        pass  # python-dotenv 未安装时忽略

_load_dotenv()

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
TESTS_DIR  = Path(__file__).parent
DOC_NAME   = "小米集团-1810-2024年年报-demo"
SOURCE_DIR = TESTS_DIR / "pdf-demo-output" / DOC_NAME
PDF_PATH   = TESTS_DIR / "pdf-demo" / f"{DOC_NAME}.pdf"
SERVER_DIR = TESTS_DIR / "server" / DOC_NAME

# 视觉模型缓存文件（避免重复 API 调用）
LLM_CACHE_FILE = SOURCE_DIR / f"{DOC_NAME}__toc_by_vision.json"

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# 1. content_list.json 工具
# ===========================================================================

def load_content_list(source_dir: Path) -> list[dict]:
    path = source_dir / "content_list.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到 content_list.json: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def find_toc_pages(items: list[dict]) -> list[int]:
    """
    从 content_list 中定位"目录页"的 page_idx 列表。
    匹配 text 中含 CONTENTS / 目录 等关键词的 text_level=1 标题。
    """
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)
    pages: list[int] = []
    for item in items:
        if item.get("type") != "text":
            continue
        text = (item.get("text") or "").strip()
        if toc_kw.search(text):
            pages.append(item["page_idx"])
    return sorted(set(pages))


def items_on_pages(items: list[dict], page_idxs: list[int]) -> list[dict]:
    """返回指定页码范围内的所有 item（page_idx 0-based）。"""
    page_set = set(page_idxs)
    return [i for i in items if i.get("page_idx") in page_set]


# ===========================================================================
# 2. 从 content_list 直接解析目录（快速路径）
# ===========================================================================

# 目录行格式："章节标题  123" 或 "章节标题 123 "
_TOC_LINE_RE = re.compile(
    r'^(?P<title>.+?)\s+(?P<page>\d+)\s*$'
)


def parse_toc_from_text_items(toc_items: list[dict]) -> list[dict]:
    """
    尝试从目录页的 text items 直接提取章节信息。
    每个 item.text 通常形如 "CHAPTER TITLE  12 "。

    返回 [{"title": "...", "page": int}, ...] 列表。
    如果解析结果为空、或有效条目数不足非空文本行的 50%（说明页码被渲染为图片），
    则返回空列表以触发视觉模型兜底。
    """
    results: list[dict] = []
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)

    non_toc_header_texts: list[str] = []   # 用于统计有效文本行数

    for item in toc_items:
        if item.get("type") != "text":
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        # 跳过目录标题行本身
        if toc_kw.search(text):
            continue
        non_toc_header_texts.append(text)
        m = _TOC_LINE_RE.match(text)
        if m:
            results.append({
                "title": m.group("title").strip(),
                "page":  int(m.group("page")),
            })

    # 品质检查：若匹配率低于 50%，说明页码大量渲染为图片，触发视觉模型
    total = len(non_toc_header_texts)
    if total > 0 and len(results) < total * 0.5:
        logger.info(
            f"快速路径仅匹配 {len(results)}/{total} 条目录项"
            "（部分页码可能渲染为图片），将使用视觉模型"
        )
        return []

    return results


# ===========================================================================
# 3. 视觉大模型识别（慢速路径）
# ===========================================================================

def render_pdf_pages_to_jpeg(
    pdf_path: Path,
    page_idxs: list[int],   # 0-based
    dpi: int = 150,
    keep_dir: Path | None = None,   # 非 None 时保留截图到指定目录
) -> list[Path]:
    """
    用 pdftoppm 把 PDF 的指定页（0-based）渲染为 JPEG，
    返回 JPEG 文件路径列表。
    keep_dir 非 None 时把截图保存到该目录（调用方负责管理），
    否则保存到临时目录（调用方负责删除）。
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
    根据环境变量构造 OpenAI-compatible 客户端。
    优先级：OPENAI_API_KEY > ANTHROPIC_AUTH_TOKEN > OPENROUTER_API_KEY
    返回 (client, model_name)。
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("请先安装 openai 包：pip install openai")

    model = os.environ.get("VISION_MODEL", "gpt-4o")

    if key := os.environ.get("OPENAI_API_KEY"):
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        client = OpenAI(api_key=key, base_url=base_url)
        logger.debug(f"使用 OpenAI 兼容接口: {base_url}, 模型: {model}")
        return client, model

    if key := os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        # 解析自定义 headers（格式: "Key: Value\nKey2: Value2"）
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
        logger.debug(f"使用 Anthropic 接口: {base_url}, 模型: {model}")
        return client, model

    if key := os.environ.get("OPENROUTER_API_KEY"):
        client = OpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
        )
        model = os.environ.get("VISION_MODEL", "openai/gpt-4o")
        logger.debug(f"使用 OpenRouter, 模型: {model}")
        return client, model

    raise RuntimeError(
        "未找到 API key。请设置 OPENAI_API_KEY / ANTHROPIC_AUTH_TOKEN / OPENROUTER_API_KEY"
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
    把一张或多张 TOC 截图发给视觉模型，返回章节列表。
    结构: [{"title": "...", "page": int}, ...]
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

    logger.info(f"调用视觉模型 [{model}] 识别 {len(jpeg_paths)} 张目录截图…")
    response = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()
    logger.debug(f"视觉模型原始返回: {raw[:300]}")

    # 去掉可能包裹的 markdown 代码块
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE)

    parsed = json.loads(raw.strip())
    if not isinstance(parsed, list):
        raise ValueError(f"视觉模型返回格式不是列表: {raw[:200]}")

    # 规范化字段
    results: list[dict] = []
    for item in parsed:
        title = str(item.get("title", "")).strip()
        page  = item.get("page")
        if title and page is not None:
            results.append({"title": title, "page": int(page)})

    logger.info(f"视觉模型识别出 {len(results)} 个章节")
    return results


# ===========================================================================
# 4. 目录获取（带缓存）
# ===========================================================================

def get_toc(
    items: list[dict],
    pdf_path: Path,
    source_dir: Path,
    force_vision: bool = False,
    keep_temp: bool = False,
) -> tuple[list[dict], bool]:
    """
    获取目录章节列表。

    返回 (chapters, used_vision)：
    - chapters    : [{"title": str, "page": int}, ...]
    - used_vision : True 表示结果来自视觉模型（或其缓存），False 表示来自文本快速路径
    """
    cache_file = LLM_CACHE_FILE

    # --- 缓存命中（来自上次视觉模型调用）---
    if cache_file.exists() and not force_vision:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(cached, list) and cached:
            logger.info(f"使用 TOC 缓存: {cache_file.name}（{len(cached)} 个章节）")
            return cached, True   # 缓存由视觉模型生成，标记为 used_vision

    # --- 定位目录页 ---
    toc_pages = find_toc_pages(items)
    if not toc_pages:
        raise ValueError("在 content_list 中未找到目录页（含 CONTENTS/目录 关键词）")
    logger.info(f"检测到目录页 page_idx: {toc_pages}")

    # --- 快速路径：直接从文本解析 ---
    if not force_vision:
        toc_items = items_on_pages(items, toc_pages)
        chapters = parse_toc_from_text_items(toc_items)
        if chapters:
            logger.info(f"从 content_list 文本直接解析出 {len(chapters)} 个章节（快速路径）")
            _save_toc_cache(cache_file, chapters)
            return chapters, False   # 文本解析，标记为非视觉
        logger.info("content_list 文本解析章节数为 0，改用视觉模型")

    # --- 慢速路径：视觉模型 ---
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"需要原始 PDF 来渲染目录截图，但找不到: {pdf_path}\n"
            "请将 PDF 放到 tests/pdf-demo/ 目录，或设置 force_vision=False 并确保 content_list 可解析。"
        )

    keep_dir = (source_dir / "debug_toc_render") if keep_temp else None
    if keep_dir:
        keep_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"--keep-temp 已启用，截图将保存到: {keep_dir}")

    jpeg_paths = render_pdf_pages_to_jpeg(pdf_path, toc_pages, dpi=150, keep_dir=keep_dir)
    logger.info(f"已渲染 {len(jpeg_paths)} 张截图: {[p.name for p in jpeg_paths]}")

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
        raise ValueError("视觉模型未能识别出任何章节，请检查 TOC 页截图或模型配置")

    _save_toc_cache(cache_file, chapters)
    return chapters, True   # 视觉模型路径


def _save_toc_cache(cache_file: Path, chapters: list[dict]) -> None:
    cache_file.write_text(
        json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"TOC 已缓存到 {cache_file.name}")


# ===========================================================================
# 5. content_list → Markdown 重建
# ===========================================================================

def prettify_html_tables(content: str) -> str:
    """展开压缩在单行的 HTML 表格为带缩进的多行格式（与 v1 保持一致）。"""

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
    """把单个 content_list item 转为 Markdown 文本。"""
    t = item.get("type", "")

    if t == "text":
        text  = (item.get("text") or "").rstrip()
        level = item.get("text_level")
        if level == 1:
            return f"# {text}\n"
        return f"{text}\n"

    if t == "header":
        # header 通常是页眉，原样保留为引用块以便区分
        text = (item.get("text") or "").strip()
        return f"> {text}\n" if text.strip("#").strip() else ""

    if t == "page_number":
        # 页码跳过
        return ""

    if t == "page_footnote":
        text = (item.get("text") or "").strip()
        return f"\n---\n*{text}*\n" if text else ""

    if t == "image":
        img_path = item.get("img_path", "")
        captions = item.get("image_caption") or []
        caption_text = " ".join(captions).strip()
        alt = caption_text or "image"
        return f"\n![{alt}]({img_path})\n"

    if t == "table":
        parts: list[str] = []
        captions = item.get("table_caption") or []
        if captions:
            parts.append("\n**" + " ".join(captions).strip() + "**\n")
        body = item.get("table_body", "")
        if body:
            parts.append(prettify_html_tables(body))
        footnotes = item.get("table_footnote") or []
        for fn in footnotes:
            if fn.strip():
                parts.append(f"*{fn.strip()}*\n")
        return "\n".join(parts) + "\n"

    if t == "list":
        lines: list[str] = []
        for li in item.get("list_items") or []:
            li = li.strip()
            if li:
                lines.append(f"- {li}")
        return "\n".join(lines) + "\n" if lines else ""

    # 未知类型降级为文本
    text = item.get("text", "")
    return f"{text}\n" if text else ""


def build_markdown_from_items(items: list[dict]) -> str:
    """把一组 content_list items 拼接为完整 Markdown 字符串。"""
    parts: list[str] = []
    for item in items:
        md = item_to_markdown(item)
        if md:
            parts.append(md)
    return "\n".join(parts)


# ===========================================================================
# 6. 按章节切割
# ===========================================================================

def chapter_to_filename(title: str) -> str:
    """章节标题 → 合法文件名。"""
    clean = re.sub(r"[^\w\s\-.]", "", title)
    clean = re.sub(r"\s+", "_", clean.strip()).lower()
    return f"{clean}.md"


def copy_images(source_dir: Path, server_dir: Path) -> None:
    """把 source_dir/images 整体复制（或合并）到 server_dir/images。"""
    src = source_dir / "images"
    if not src.is_dir():
        logger.debug("无 images 子目录，跳过")
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
        logger.info(f"已复制 images → {dst}")


def split_items_by_chapters(
    all_items: list[dict],
    toc: list[dict],
) -> dict[str, list[dict]]:
    """
    按 TOC 章节把 content_list items 分组。

    匹配策略（按优先级尝试）：
    1. 单个 text_level=1 item 的 text 与章节标题完全匹配（忽略首尾空格、大小写）。
    2. 同一页上相邻的若干 text_level=1 items 拼接后与章节标题匹配
       （处理标题被拆成多行的情况，如 "FIVE-YEAR " + "FINANCIAL SUMMARY "）。

    返回字典，固定包含以下键：
    - "pre_toc"  : TOC 页之前的 items（封面、股东通知等）
    - "toc_page" : TOC 页本身的 items（原始文本/图片，仅供参考，不直接写出）
    - 章节标题   : 对应章节的 items
    """

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip()).lower()

    toc_titles = [entry["title"] for entry in toc]
    toc_norm   = {_norm(t): t for t in toc_titles}

    # 找出 TOC 页的 page_idx（含 CONTENTS/目录 关键词的页）
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)
    toc_page_idxs: set[int] = set()
    for item in all_items:
        if item.get("type") == "text" and toc_kw.search((item.get("text") or "").strip()):
            toc_page_idxs.add(item["page_idx"])

    # ---- 预处理：提取所有 text_level=1 的 item 位置（排除 TOC 页自身）----
    heading_items: list[tuple[int, dict]] = [
        (idx, item)
        for idx, item in enumerate(all_items)
        if item.get("type") == "text"
        and item.get("text_level") == 1
        and item.get("page_idx") not in toc_page_idxs
    ]

    # ---- 按页分组，尝试拼接匹配章节标题 ----
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

    # 确定第一个章节的 item 位置（无匹配则取末尾）
    first_chapter_idx = chapter_starts[0][0] if chapter_starts else len(all_items)

    # pre_toc：TOC 页之前的 items
    result["pre_toc"] = [
        item for item in all_items[:first_chapter_idx]
        if item.get("page_idx") not in toc_page_idxs
    ]

    # toc_page：TOC 页本身的原始 items（保留备查，不直接输出）
    result["toc_page"] = [
        item for item in all_items
        if item.get("page_idx") in toc_page_idxs
    ]

    # 各章节 items
    for i, (start_idx, title) in enumerate(chapter_starts):
        end_idx = chapter_starts[i + 1][0] if i + 1 < len(chapter_starts) else len(all_items)
        result[title] = all_items[start_idx:end_idx]

    missed = [t for t in toc_titles if t not in already_matched]
    if missed:
        logger.warning(f"以下 {len(missed)} 个章节在 content_list 中未找到匹配（可能不在 demo 范围内）：")
        for t in missed:
            logger.warning(f"  - {t!r}")

    return result


# ===========================================================================
# 7. 主流程
# ===========================================================================

def build_toc_md(toc: list[dict], toc_page_items: list[dict], used_vision: bool) -> str:
    """
    生成 toc.md 的内容。

    两种场景：
    - used_vision=True（或快速路径补全了所有条目）：
        用 toc 列表直接生成 Markdown 表格，每个章节标题带相对链接。
    - 快速路径（used_vision=False）：
        先渲染 content_list 里 TOC 页的原始文本（保留原有排版），
        再在下方附加带链接的导航表格。
        原始文本区的章节名如能匹配到，也内联替换为链接。

    toc_page_items：split_items_by_chapters 返回的 "toc_page" 段。
    """
    toc_kw = re.compile(r'目\s*录|^contents\s*$|table\s+of\s+contents', re.IGNORECASE)

    # ---- 构建链接表格（两种场景都输出）----
    link_rows: list[str] = [
        "| 章节 | 页码 |",
        "| --- | --- |",
    ]
    for entry in toc:
        filename = chapter_to_filename(entry["title"])
        link_rows.append(f"| [{entry['title']}](./{filename}) | {entry['page']} |")
    link_table = "\n".join(link_rows)

    if used_vision:
        # 视觉模型路径：只输出干净的链接表格（原 TOC 页文本不可信/不完整）
        return f"# Table of Contents\n\n{link_table}\n"

    # 快速路径：先输出原始 TOC 页文本（内联章节链接），再附加完整导航表格
    # 构建 title → filename 映射，用于内联替换
    title_to_file = {e["title"]: chapter_to_filename(e["title"]) for e in toc}

    original_lines: list[str] = []
    for item in toc_page_items:
        if item.get("type") != "text":
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        if toc_kw.search(text):
            # 目录标题行本身，渲染为 H1
            original_lines.append(f"# {text}\n")
            continue
        # 尝试把行内的章节名替换为链接（匹配"标题  页码"格式）
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
    基于 content_list.json 把年报拆分输出到 server_dir。

    生成文件：
    - server_dir/index.md    封面前言（TOC 页之前的内容，不含目录）
    - server_dir/toc.md      导航目录（章节链接表格；快速路径还保留原始目录排版）
    - server_dir/<ch>.md     各章节正文
    - server_dir/images/     图片资源

    参数：
        force_vision  True = 跳过快速文本解析，强制走视觉模型
        keep_temp     True = 保留 PDF 渲染截图到 source_dir/debug_toc_render/，便于排查
    """
    logger.info(f"源目录  : {source_dir}")
    logger.info(f"PDF     : {pdf_path}")
    logger.info(f"输出目录: {server_dir}")

    # 1. 加载 content_list
    items = load_content_list(source_dir)
    logger.info(f"已加载 content_list，共 {len(items)} 个 item")

    # 2. 获取目录
    toc, used_vision = get_toc(items, pdf_path, source_dir, force_vision=force_vision, keep_temp=keep_temp)

    logger.info(f"目录章节（共 {len(toc)} 个，来源：{'视觉模型/缓存' if used_vision else '文本解析'}）：")
    for entry in toc:
        logger.info(f"  p.{entry['page']:>4}  {entry['title']}")

    # 3. 创建输出目录
    server_dir.mkdir(parents=True, exist_ok=True)

    # 4. 按章节分组 items
    sections = split_items_by_chapters(items, toc)
    matched_count = sum(1 for k in sections if k not in ("pre_toc", "toc_page"))
    logger.info(f"成功匹配 {matched_count} 个章节")

    # 5. 写 index.md（纯前言：TOC 页之前的内容）
    pre_toc_items = sections.get("pre_toc", [])
    index_md = build_markdown_from_items(pre_toc_items).strip()
    index_path = server_dir / "index.md"
    index_path.write_text(index_md + "\n", encoding="utf-8")
    logger.info(f"已写入 index.md（{len(index_md)} 字节，{len(pre_toc_items)} items）")

    # 6. 写 toc.md（带链接的导航目录）
    toc_page_items = sections.get("toc_page", [])
    toc_md = build_toc_md(toc, toc_page_items, used_vision=used_vision)
    toc_path = server_dir / "toc.md"
    toc_path.write_text(toc_md, encoding="utf-8")
    logger.info(f"已写入 toc.md（{'视觉模型链接表格' if used_vision else '原始文本 + 链接表格'}）")

    # 7. 写各章节 .md
    saved = 0
    for entry in toc:
        title    = entry["title"]
        filename = chapter_to_filename(title)
        chapter_items = sections.get(title)

        if not chapter_items:
            logger.warning(f"  [跳过] 无内容: {title!r}")
            continue

        chapter_md = build_markdown_from_items(chapter_items)
        out_path = server_dir / filename
        out_path.write_text(chapter_md.strip() + "\n", encoding="utf-8")
        logger.info(f"  已写入 {filename}（{len(chapter_md)} 字节，{len(chapter_items)} items）")
        saved += 1

    logger.info(f"章节写入完成：{saved} / {len(toc)}")

    # 8. 复制图片
    copy_images(source_dir, server_dir)

    logger.info("✓ 全部完成")


# ===========================================================================
# 入口
# ===========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="add_md_to_server_v2: 基于 content_list.json 拆分年报")
    parser.add_argument("--doc",          default=DOC_NAME,        help="文档名称（pdf-demo-output 子目录名）")
    parser.add_argument("--force-vision", action="store_true",     help="强制走视觉模型，跳过文本解析快速路径")
    parser.add_argument("--keep-temp",    action="store_true",     help="保留 PDF 渲染截图到 source_dir/debug_toc_render/，便于排查")
    parser.add_argument("--vision-model", default=None,            help="覆盖 VISION_MODEL 环境变量")
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

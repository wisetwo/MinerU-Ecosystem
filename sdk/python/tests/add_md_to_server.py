#!/usr/bin/env python3
# 需要 Python 3.11+（使用了 X | Y 联合类型注解语法）
# -*- coding: utf-8 -*-
"""
把 pdf-demo-output 中的 Markdown 文件按目录章节拆分，
输出到 server/{doc_name}/ 目录，并同步复制 images 目录。

用法：
    python add_md_to_server.py

源文件：
    tests/pdf-demo-output/拼多多-PDD-2024年年报-demo/拼多多-PDD-2024年年报-demo.md

输出目录：
    tests/server/拼多多-PDD-2024年年报-demo/
"""

import re
import shutil
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).parent

SOURCE_DIR = TESTS_DIR / "pdf-demo-output" / "拼多多-PDD-2024年年报-demo"
DOC_NAME   = "拼多多-PDD-2024年年报-demo"
MAIN_MD    = SOURCE_DIR / f"{DOC_NAME}.md"
SERVER_DIR = TESTS_DIR / "server" / DOC_NAME

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TOC 解析：从 HTML <table> 中提取章节标题
# ---------------------------------------------------------------------------

# 跳过这些纯分组行（不是可定位的章节）
_PART_RE = re.compile(r'^Part\s+[IVX]+$', re.IGNORECASE)


def parse_toc_from_html_table(content: str) -> list[str]:
    """
    从 Markdown 内容里的 HTML <table>（即 TABLE OF CONTENTS）解析章节列表。

    目录 HTML 样例：
        <table>
          <tr><td colspan="2">INTRODUCTION</td><td>1</td></tr>
          <tr><td></td><td>Item 1. Identity of Directors ...</td><td>3</td></tr>
          ...
        </table>

    规则：
    - colspan="2" 的单元格视为顶级项（保留非 "Part I/II/III" 的条目）
    - 第二列（没有 colspan）视为子项
    - 页码列（纯数字）忽略
    """
    # 定位目录 table
    toc_block_match = re.search(
        r'TABLE OF CONTENTS.*?(<table>.*?</table>)',
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if not toc_block_match:
        raise ValueError("在 Markdown 内容中未找到 TABLE OF CONTENTS 的 <table> 块")

    table_html = toc_block_match.group(1)

    # 提取所有 <td> 内容
    chapters: list[str] = []
    for row_html in re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
        # 过滤空单元格和纯数字（页码）
        cells = [c.strip() for c in cells if c.strip() and not re.fullmatch(r'\d+', c.strip())]

        for cell in cells:
            # 跳过 Part I / Part II 等分组行
            if _PART_RE.match(cell):
                continue
            chapters.append(cell)

    return chapters


# ---------------------------------------------------------------------------
# 章节内容定位
# ---------------------------------------------------------------------------

def _make_heading_pattern(chapter: str) -> str:
    """构造匹配 Markdown H1/H2/H3 标题的正则，支持宽松空白。"""
    escaped = re.escape(chapter)
    # 允许标题前后有可选空白，标题级别为 #、##、###
    return r'^#{1,3}\s+' + escaped + r'\s*$'


def find_chapter_boundary(content: str, chapter: str) -> re.Match | None:
    """在 content 中寻找章节标题行，返回 Match 对象；未找到返回 None。"""
    pattern = _make_heading_pattern(chapter)
    return re.search(pattern, content, re.MULTILINE | re.IGNORECASE)


def extract_chapter_content(
    full_content: str,
    chapter: str,
    next_chapter: str | None,
) -> str | None:
    """
    从 full_content 中截取 chapter 的正文（到 next_chapter 之前）。
    返回截取的字符串，未找到则返回 None。
    """
    start_match = find_chapter_boundary(full_content, chapter)
    if not start_match:
        logger.debug(f"  未找到章节标题：{chapter!r}")
        return None

    start_pos = start_match.start()

    if next_chapter:
        # 在 start_pos 之后的内容里寻找下一章
        remaining = full_content[start_pos:]
        end_match = find_chapter_boundary(remaining, next_chapter)
        if end_match:
            return remaining[: end_match.start()].strip()

    return full_content[start_pos:].strip()


# ---------------------------------------------------------------------------
# 文件名清理
# ---------------------------------------------------------------------------

def chapter_to_filename(chapter: str) -> str:
    """将章节名转换为合法的文件名（保留 ASCII 字母数字、空格、连字符）。"""
    clean = re.sub(r'[^\w\s\-.]', '', chapter)          # 去掉特殊符号
    clean = re.sub(r'\s+', '_', clean.strip())           # 空白 → 下划线
    clean = clean.lower()
    return f"{clean}.md"


# ---------------------------------------------------------------------------
# HTML 表格美化
# ---------------------------------------------------------------------------

def prettify_html_tables(content: str) -> str:
    """
    把 Markdown 内容中所有压缩在单行的 HTML 表格展开为带缩进的多行格式。

    输入示例：
        <table><tr><td colspan="2">A</td><td>1</td></tr><tr><td>B</td></tr></table>

    输出示例：
        <table>
          <tr>
            <td colspan="2">A</td>
            <td>1</td>
          </tr>
          <tr>
            <td>B</td>
          </tr>
        </table>
    """

    def _prettify_one(m: re.Match) -> str:
        raw = m.group(0)

        # 在各标签前插入换行，再统一处理缩进
        # 先把所有 > 后紧跟 < 的地方断开
        spaced = re.sub(r'>\s*<', '>\n<', raw)

        lines = spaced.split('\n')
        result: list[str] = []
        indent = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 纯闭合标签（</table>、</tr>、</td> 等）先减缩进再输出
            if re.match(r'^</(table|thead|tbody|tfoot|tr|th|td)>', line, re.IGNORECASE):
                # 若上一行是纯空内容行（即上一个开标签后跟着闭标签），合并为单行
                if result:
                    prev = result[-1].strip()
                    open_only = re.match(r'^<(td|th)(\s[^>]*)?>$', prev, re.IGNORECASE)
                    if open_only:
                        # 合并：把前一行的开标签与本闭标签拼在同一行
                        result[-1] = result[-1] + line
                        indent -= 1
                        continue
                indent -= 1

            result.append('  ' * indent + line)

            # 开标签（不是自闭合）且不含对应闭合标签 → 增加缩进
            open_tag = re.match(r'^<(table|thead|tbody|tfoot|tr|th|td)(\s[^>]*)?>(?!.*</\1>)', line, re.IGNORECASE)
            if open_tag:
                indent += 1

        return '\n' + '\n'.join(result) + '\n'

    # 匹配整个 <table>…</table> 块（允许跨行）
    return re.sub(r'<table>.*?</table>', _prettify_one, content, flags=re.DOTALL | re.IGNORECASE)


# ---------------------------------------------------------------------------
# 图片目录复制
# ---------------------------------------------------------------------------

def copy_images(source_dir: Path, server_dir: Path) -> None:
    """把 source_dir/images 整体复制（或合并）到 server_dir/images。"""
    src_images = source_dir / "images"
    if not src_images.is_dir():
        logger.debug("源目录中没有 images 子目录，跳过复制")
        return

    dst_images = server_dir / "images"
    if dst_images.exists():
        # 已存在则逐文件合并（不覆盖）
        for item in src_images.iterdir():
            if item.is_file():
                dst_file = dst_images / item.name
                if not dst_file.exists():
                    shutil.copy2(item, dst_file)
                    logger.debug(f"  复制图片: {item.name}")
    else:
        shutil.copytree(src_images, dst_images)
        logger.info(f"已复制 images 目录 → {dst_images}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def add_md_to_server(
    main_md: Path = MAIN_MD,
    source_dir: Path = SOURCE_DIR,
    server_dir: Path = SERVER_DIR,
) -> None:
    """
    把 main_md 按目录章节拆分后，输出到 server_dir。

    生成文件：
    - server_dir/index.md         — 封面 + 目录（含章节链接）
    - server_dir/<chapter>.md     — 各章节正文
    - server_dir/images/          — 图片资源
    """
    logger.info(f"源文件 : {main_md}")
    logger.info(f"输出目录: {server_dir}")

    # 1. 读取 Markdown
    content = main_md.read_text(encoding="utf-8")
    logger.info(f"已读取文件，共 {len(content)} 字节")

    # 美化所有 HTML 表格（展开为带缩进的多行格式）
    content = prettify_html_tables(content)
    logger.info("已完成 HTML 表格美化")

    # 2. 解析目录
    chapters = parse_toc_from_html_table(content)
    logger.info(f"从 TOC 解析出 {len(chapters)} 个章节")
    for i, ch in enumerate(chapters, 1):
        logger.debug(f"  [{i:02d}] {ch}")

    if not chapters:
        raise ValueError("未解析到任何章节，请检查 TOC 格式")

    # 3. 创建输出目录
    server_dir.mkdir(parents=True, exist_ok=True)

    # 4. 构建 index.md（封面 + TOC）
    #    取文件开头到第一个章节标题之前的内容
    first_chapter_match = find_chapter_boundary(content, chapters[0])
    if first_chapter_match is None:
        raise ValueError(f"在内容中找不到第一个章节标题：{chapters[0]!r}")

    index_content = content[: first_chapter_match.start()].strip()

    # 在 index_content 中把章节名替换成相对链接（适配 HTML table 格式）
    for chapter in chapters:
        filename = chapter_to_filename(chapter)
        # 替换 <td>章节名</td> → <td><a href="./filename">章节名</a></td>
        index_content = index_content.replace(
            f"<td>{chapter}</td>",
            f'<td><a href="./{filename}">{chapter}</a></td>',
        )

    index_path = server_dir / "index.md"
    index_path.write_text(index_content + "\n", encoding="utf-8")
    logger.info(f"已生成 {index_path.name}（{len(index_content)} 字节）")

    # 5. 拆分章节，写入各 chapter 文件
    remaining_content = content[first_chapter_match.start():]
    saved = 0
    failed = 0

    for i, chapter in enumerate(chapters):
        next_chapter = chapters[i + 1] if i < len(chapters) - 1 else None
        chapter_content = extract_chapter_content(remaining_content, chapter, next_chapter)

        if chapter_content is None:
            logger.warning(f"  [跳过] 未找到章节内容：{chapter!r}")
            failed += 1
            continue

        # 将原来的 # Heading 级别提升为 ## Heading（顶级标题）
        lines = chapter_content.split('\n')
        if lines and re.match(r'^#{1,3}\s+', lines[0]):
            heading_text = re.sub(r'^#{1,3}\s+', '', lines[0])
            lines[0] = f"## {heading_text}"
        chapter_content = '\n'.join(lines)

        filename = chapter_to_filename(chapter)
        out_path = server_dir / filename
        out_path.write_text(chapter_content.strip() + "\n", encoding="utf-8")
        logger.info(f"  [{i+1:02d}/{len(chapters)}] {filename}（{len(chapter_content)} 字节）")
        saved += 1

    logger.info(f"章节写入完成：成功 {saved} / 跳过 {failed} / 共 {len(chapters)}")

    # 6. 复制图片目录
    copy_images(source_dir, server_dir)

    logger.info("✓ 全部完成")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    add_md_to_server()

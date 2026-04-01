"""HTML table → GitHub-Flavoured Markdown converter (pure stdlib).

Handles colspan and rowspan by expanding cells into a 2-D grid before
rendering, which ensures column counts stay consistent across rows.

纯 stdlib 实现的 HTML 表格转 GFM Markdown 转换器。
通过将单元格展开为二维网格来处理 colspan 和 rowspan，
确保各行列数一致。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Internal grid builder
# ---------------------------------------------------------------------------

class _Cell:
    """Represents a single logical cell in the grid (after span expansion)."""

    __slots__ = ("text", "is_header")

    def __init__(self, text: str, is_header: bool = False) -> None:
        self.text = text
        self.is_header = is_header


class _TableParser(HTMLParser):
    """SAX-style parser that builds a 2-D grid from an HTML <table>.

    Algorithm:
    1. Maintain a *grid* (list of rows, each row is a list of Optional[_Cell]).
    2. When a <td> or <th> is opened, find the first free slot in the
       current row (skipping slots already filled by rowspans from above).
    3. Fill colspan × rowspan slots with the cell content.
    4. Collect text content of each cell, stripping inner tags.
    """

    def __init__(self) -> None:
        super().__init__()
        self.grid: List[List[Optional[_Cell]]] = []
        self._row_idx: int = -1
        self._col_idx: int = -1
        self._in_cell: bool = False
        self._is_header: bool = False
        self._cell_text: List[str] = []
        self._rowspan_map: Dict[int, int] = {}  # col → remaining rows
        self._colspan: int = 1
        self._rowspan: int = 1
        self._in_table: bool = False

    # ------------------------------------------------------------------
    # HTMLParser callbacks
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = dict(attrs)
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._row_idx += 1
            self.grid.append([])
            self._col_idx = -1
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = True
            self._is_header = tag == "th"
            self._cell_text = []
            self._colspan = int(attr.get("colspan", 1))
            self._rowspan = int(attr.get("rowspan", 1))
        elif tag == "br" and self._in_cell:
            self._cell_text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            text = _normalise_cell("".join(self._cell_text))
            self._place_cell(text)
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)

    # ------------------------------------------------------------------
    # Grid placement
    # ------------------------------------------------------------------

    def _place_cell(self, text: str) -> None:
        """Place the current cell (with colspan/rowspan) into *self.grid*."""
        row = self.grid[self._row_idx]

        # Find the next free column in the current row
        col_start = 0
        used = {i for i, c in enumerate(row) if c is not None}
        # Also skip columns reserved by rowspans
        while col_start in used or self._rowspan_map.get(col_start, 0) > 0:
            col_start += 1

        cell = _Cell(text, self._is_header)

        # Expand colspan × rowspan
        for r_offset in range(self._rowspan):
            abs_row = self._row_idx + r_offset
            # Ensure rows exist
            while len(self.grid) <= abs_row:
                self.grid.append([])
            target_row = self.grid[abs_row]
            for c_offset in range(self._colspan):
                abs_col = col_start + c_offset
                # Extend the row if needed
                while len(target_row) <= abs_col:
                    target_row.append(None)
                target_row[abs_col] = cell

        # Update rowspan tracking for future rows
        if self._rowspan > 1:
            for c_offset in range(self._colspan):
                abs_col = col_start + c_offset
                self._rowspan_map[abs_col] = (
                    self._rowspan_map.get(abs_col, 0) + self._rowspan - 1
                )
        # Decrement existing rowspan counters for columns before col_start
        to_del = []
        for col, remaining in self._rowspan_map.items():
            if col < col_start:
                new_val = remaining - 1
                if new_val <= 0:
                    to_del.append(col)
                else:
                    self._rowspan_map[col] = new_val
        for col in to_del:
            del self._rowspan_map[col]


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _normalise_cell(text: str) -> str:
    """Strip extra whitespace and replace newlines with spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _pad_row(row: List[Optional[_Cell]], n_cols: int) -> List[Optional[_Cell]]:
    """Right-pad a row with None so it has exactly *n_cols* entries."""
    if len(row) < n_cols:
        return row + [None] * (n_cols - len(row))
    return row[:n_cols]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def html_table_to_markdown(html: str) -> str:
    """Convert an HTML ``<table>`` string to a GFM Markdown table.

    将 HTML ``<table>`` 字符串转换为 GFM Markdown 表格。

    Features:
    - Expands colspan and rowspan into the 2-D grid
    - Strips inner HTML tags from cells
    - Emits a header separator after the first row (GFM requirement)
    - Cells containing ``|`` are escaped to ``\\|``

    Args:
        html: A string that contains at least one ``<table>`` element.

    Returns:
        A GFM Markdown table string, or the original *html* if no table
        could be parsed.
    """
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception:
        return html

    grid = parser.grid
    if not grid:
        return html

    # Drop rows that are entirely None (rowspan pre-allocation artifacts)
    grid = [row for row in grid if any(c is not None for c in row)]
    if not grid:
        return html

    # Determine the maximum column count across all rows
    n_cols = max((len(row) for row in grid), default=0)
    if n_cols == 0:
        return html

    def _render_cell(cell: Optional[_Cell]) -> str:
        if cell is None:
            return ""
        return cell.text.replace("|", "\\|")

    lines: List[str] = []
    for row_idx, row in enumerate(grid):
        padded = _pad_row(row, n_cols)
        cells = [_render_cell(c) for c in padded]
        lines.append("| " + " | ".join(cells) + " |")
        # Insert GFM header separator after the first row
        if row_idx == 0:
            lines.append("| " + " | ".join(["---"] * n_cols) + " |")

    return "\n".join(lines)

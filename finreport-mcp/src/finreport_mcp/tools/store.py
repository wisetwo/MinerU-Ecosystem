"""DocumentStore — thread-safe LRU cache for MinerU content_list.json files."""

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ContentList = List[Dict[str, Any]]


class DocumentStore:
    """Thread-safe LRU cache for loaded content_list.json documents.

    The cache uses a double-check pattern to avoid redundant I/O under
    concurrent access: file I/O is performed *outside* the lock, and
    the result is only inserted after re-acquiring the lock and verifying
    the key is still absent.
    """

    def __init__(self, max_size: int = 10) -> None:
        self._max_size = max_size
        self._cache: OrderedDict[str, ContentList] = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, report_dir: str) -> str:
        """Normalize *report_dir* to an absolute POSIX path string.

        Using Path.resolve() ensures that two callers passing the same
        directory with different relative/absolute representations hit the
        same cache slot.
        """
        return str(Path(report_dir).resolve())

    def _find_content_list(self, resolved_dir: str) -> Path:
        """Locate the content_list.json file inside *resolved_dir*.

        Raises FileNotFoundError if no suitable file is found.
        """
        base = Path(resolved_dir)
        # Prefer auto_content_list.json, then content_list.json
        for name in ("auto_content_list.json", "content_list.json"):
            candidate = base / name
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"No content_list.json (or auto_content_list.json) found in: {resolved_dir}"
        )

    def _evict_if_needed(self) -> None:
        """Evict the least-recently-used entry when the cache is full.

        Must be called while holding *_lock*.
        """
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, report_dir: str) -> ContentList:
        """Return the content list for *report_dir*, loading it if necessary.

        Args:
            report_dir: Path to the directory containing content_list.json.

        Returns:
            A list of element dicts as parsed from content_list.json.

        Raises:
            FileNotFoundError: If no content_list.json is found.
            json.JSONDecodeError: If the file cannot be parsed.
        """
        key = self._resolve(report_dir)

        # Fast path — cache hit
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        # Slow path — load from disk *outside* the lock
        json_path = self._find_content_list(key)
        data: ContentList = json.loads(json_path.read_text(encoding="utf-8"))

        # Double-check: insert only if still absent
        with self._lock:
            if key not in self._cache:
                self._evict_if_needed()
                self._cache[key] = data
            else:
                # Another thread loaded it while we were doing I/O
                self._cache.move_to_end(key)
            return self._cache[key]

    def get_element(self, report_dir: str, index: int) -> Dict[str, Any]:
        """Return a single element by its list index.

        Args:
            report_dir: Path to the report directory.
            index: Zero-based element index.

        Returns:
            The element dict at position *index*.

        Raises:
            IndexError: If *index* is out of range.
        """
        content = self.get(report_dir)
        if index < 0 or index >= len(content):
            raise IndexError(
                f"Element index {index} is out of range (document has {len(content)} elements)."
            )
        return content[index]

    def get_page_elements(
        self, report_dir: str, page_idx: int
    ) -> Tuple[List[Tuple[int, Dict[str, Any]]], int]:
        """Return all elements on a given page together with the total page count.

        Args:
            report_dir: Path to the report directory.
            page_idx: Zero-based page index.

        Returns:
            A tuple of:
              - List of (element_index, element_dict) pairs for *page_idx*
              - Total number of pages in the document
        """
        content = self.get(report_dir)
        total_pages = max((e.get("page_idx", 0) for e in content), default=0) + 1
        elements = [
            (i, elem)
            for i, elem in enumerate(content)
            if elem.get("page_idx") == page_idx
        ]
        return elements, total_pages

    def invalidate(self, report_dir: str) -> bool:
        """Remove a single entry from the cache.

        Returns:
            True if the entry was present and removed, False otherwise.
        """
        key = self._resolve(report_dir)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._cache.clear()

    def cached_dirs(self) -> List[str]:
        """Return a snapshot of currently cached directory paths."""
        with self._lock:
            return list(self._cache.keys())

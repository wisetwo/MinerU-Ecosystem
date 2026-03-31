"""Batch PDF-demo → Markdown test — high-accuracy (VLM) mode.

This script reads every PDF in ``tests/pdf-demo/``, sends them to the MinerU
API using the high-accuracy **vlm** model, and saves full results into
per-PDF output directories.

Output structure::

    tests/pdf-demo-output/
    ├── 小米集团-1810-2024年年报-demo/
    │   ├── 小米集团-1810-2024年年报-demo.md      # Markdown
    │   ├── content_list.json                     # Structured content list
    │   ├── images/                               # Extracted images
    │   │   ├── img_0.jpg
    │   │   └── ...
    │   └── raw/                                  # Full original zip contents
    │       └── ...
    └── 拼多多-PDD-2024年年报-demo/
        └── ...

API Token configuration (pick ONE):
  1. Environment variable:  export MINERU_TOKEN="your-token-here"
  2. .env file:             create ``sdk/python/.env`` with MINERU_TOKEN=your-token
  3. Direct in code:        MinerU(token="your-token-here")  (not recommended)

Usage:
  cd sdk/python
  # Make sure MINERU_TOKEN is set (env var or .env file)
  uv run pytest tests/test_pdf_demo_batch.py -v -s
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mineru import ExtractResult, MinerU

# ── Paths ──
TESTS_DIR = Path(__file__).resolve().parent
PDF_DEMO_DIR = TESTS_DIR / "pdf-demo"
OUTPUT_DIR = TESTS_DIR / "pdf-demo-output"

# ── Settings ──
# High-accuracy model
MODEL = "vlm"
# Document language code — explicitly set to "ch" for Chinese/English/Traditional Chinese.
# This is critical for correct CJK character recognition. Supported values:
#   "ch"           — Chinese, English, Chinese Traditional (default on API but best to be explicit)
#   "chinese_cht"  — Chinese, English, Chinese Traditional, Japanese
#   "en"           — English only
#   "japan"        — Chinese, English, Chinese Traditional, Japanese
#   "korean"       — Korean, English
# See mcp/src/mineru_open_mcp/language.py for the full list.
LANGUAGE = "ch"
# Enable OCR for scanned PDFs or images with embedded text
ENABLE_OCR = True
# Generous timeout for large annual reports (20 min per file)
SINGLE_TIMEOUT = 1200
BATCH_TIMEOUT = 2400


def _load_dotenv() -> None:
    """Minimal .env loader — no extra dependency needed.

    Reads ``sdk/python/.env`` and injects variables into ``os.environ``
    if they are not already set.
    """
    env_file = TESTS_DIR.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


# Load .env before any test runs
_load_dotenv()


def _collect_pdfs() -> list[Path]:
    """Return all PDF files in the pdf-demo/ directory."""
    if not PDF_DEMO_DIR.is_dir():
        return []
    return sorted(PDF_DEMO_DIR.glob("*.pdf"))


def _save_result(result: ExtractResult, output_dir: Path, stem: str) -> dict:
    """Save all result data to a per-PDF directory. Returns a summary dict.

    Directory layout:
        {output_dir}/{stem}/
        ├── {stem}.md              # Markdown output
        ├── content_list.json      # Structured element list (if available)
        ├── metadata.json          # Task metadata (task_id, state, etc.)
        ├── images/                # Extracted images (if any)
        │   ├── img_0.jpg
        │   └── ...
        └── raw/                   # Full original API zip contents
            └── ...
    """
    pdf_dir = output_dir / stem
    pdf_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {"stem": stem, "task_id": result.task_id, "state": result.state}

    # ── 1. Markdown ──
    if result.markdown is not None:
        md_path = pdf_dir / f"{stem}.md"
        md_path.write_text(result.markdown, encoding="utf-8")
        summary["markdown_size"] = len(result.markdown)

    # ── 2. Images (per-PDF isolated images/ directory) ──
    if result.images:
        img_dir = pdf_dir / "images"
        img_dir.mkdir(exist_ok=True)
        for img in result.images:
            (img_dir / img.name).write_bytes(img.data)
        summary["image_count"] = len(result.images)

    # ── 3. content_list.json — structured intermediate data ──
    if result.content_list is not None:
        cl_path = pdf_dir / "content_list.json"
        cl_path.write_text(
            json.dumps(result.content_list, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary["content_list_items"] = len(result.content_list)

    # ── 4. Metadata (task-level info for later inspection) ──
    meta = {
        "task_id": result.task_id,
        "state": result.state,
        "filename": result.filename,
        "err_code": result.err_code,
        "error": result.error,
        "zip_url": result.zip_url,
        "progress": (
            {
                "extracted_pages": result.progress.extracted_pages,
                "total_pages": result.progress.total_pages,
                "start_time": result.progress.start_time,
            }
            if result.progress
            else None
        ),
        "has_markdown": result.markdown is not None,
        "has_content_list": result.content_list is not None,
        "image_count": len(result.images),
        "has_docx": result.docx is not None,
        "has_html": result.html is not None,
        "has_latex": result.latex is not None,
    }
    meta_path = pdf_dir / "metadata.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── 5. Raw zip contents (full API output) ──
    if result._zip_bytes is not None:
        raw_dir = pdf_dir / "raw"
        result.save_all(str(raw_dir))
        summary["raw_extracted"] = True

    return summary


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def client():
    """Create a MinerU client; skip the whole module if no token."""
    token = os.environ.get("MINERU_TOKEN")
    if not token:
        pytest.skip(
            "MINERU_TOKEN not set. "
            "Export it or create sdk/python/.env with MINERU_TOKEN=your-token"
        )
    c = MinerU(token=token)
    yield c
    c.close()


@pytest.fixture(scope="module")
def pdf_files():
    """Collect PDF files; skip if none found."""
    files = _collect_pdfs()
    if not files:
        pytest.skip(f"No PDF files found in {PDF_DEMO_DIR}")
    return files


# ═══════════════════════════════════════════════════════════════════
#  Tests
# ═══════════════════════════════════════════════════════════════════


class TestPdfDemoBatchConvert:
    """Batch-convert all PDFs in pdf-demo/ to Markdown (VLM high-accuracy)."""

    def test_batch_extract_returns_results(self, client, pdf_files):
        """All PDFs should be processed and return ExtractResult objects."""
        sources = [str(p) for p in pdf_files]
        results = list(
            client.extract_batch(
                sources,
                model=MODEL,
                language=LANGUAGE,
                ocr=ENABLE_OCR,
                timeout=BATCH_TIMEOUT,
            )
        )
        assert len(results) == len(pdf_files), (
            f"Expected {len(pdf_files)} results, got {len(results)}"
        )
        for r in results:
            assert isinstance(r, ExtractResult)

    def test_all_results_done_with_markdown(self, client, pdf_files):
        """Every result should be done and contain non-empty markdown."""
        sources = [str(p) for p in pdf_files]
        results = list(
            client.extract_batch(
                sources,
                model=MODEL,
                language=LANGUAGE,
                ocr=ENABLE_OCR,
                timeout=BATCH_TIMEOUT,
            )
        )
        for r in results:
            assert r.state == "done", (
                f"Task {r.task_id} failed: err_code={r.err_code}, error={r.error}"
            )
            assert r.markdown is not None and len(r.markdown) > 0, (
                f"Task {r.task_id} has empty markdown"
            )


class TestPdfDemoSingleConvert:
    """Convert each PDF individually and save full results per PDF."""

    @pytest.fixture(autouse=True)
    def _setup_output_dir(self):
        """Ensure the output directory exists."""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def test_convert_and_save_each_pdf(self, client, pdf_files):
        """Convert each PDF one-by-one, save all outputs to per-PDF dirs."""
        succeeded = []
        failed = []

        for pdf_path in pdf_files:
            stem = pdf_path.stem
            print(f"\n{'─'*60}")
            print(f"  Converting: {pdf_path.name}")
            print(f"  Model:      {MODEL} (high-accuracy)")
            print(f"  Language:   {LANGUAGE}")
            print(f"  OCR:        {ENABLE_OCR}")
            print(f"{'─'*60}")

            try:
                result = client.extract(
                    str(pdf_path),
                    model=MODEL,
                    language=LANGUAGE,
                    ocr=ENABLE_OCR,
                    timeout=SINGLE_TIMEOUT,
                )

                assert result.state == "done", (
                    f"[{pdf_path.name}] state={result.state}, "
                    f"err_code={result.err_code}, error={result.error}"
                )
                assert result.markdown is not None

                # Save all results to isolated per-PDF directory
                summary = _save_result(result, OUTPUT_DIR, stem)

                print(f"  ✅ Output:  {OUTPUT_DIR / stem}/")
                print(f"     Markdown: {summary.get('markdown_size', 0):,} bytes")
                if summary.get("image_count"):
                    print(f"     Images:   {summary['image_count']} files")
                if summary.get("content_list_items"):
                    print(f"     Content list: {summary['content_list_items']} elements")
                if summary.get("raw_extracted"):
                    print(f"     Raw zip:  extracted to raw/")
                succeeded.append(pdf_path.name)

            except Exception as e:
                print(f"  ❌ Failed: {pdf_path.name} — {e}")
                failed.append((pdf_path.name, str(e)))

        # Summary
        print(f"\n{'═'*60}")
        print(f"  SUMMARY")
        print(f"{'═'*60}")
        print(f"  Total:     {len(pdf_files)}")
        print(f"  Succeeded: {len(succeeded)}")
        print(f"  Failed:    {len(failed)}")
        if failed:
            for name, err in failed:
                print(f"    - {name}: {err}")
        print(f"  Output:    {OUTPUT_DIR}/")
        print(f"{'═'*60}\n")

        assert len(failed) == 0, f"{len(failed)} file(s) failed: {failed}"

"""Batch PDF-demo → Markdown test — high-accuracy (VLM) mode.

This script reads every PDF in ``tests/pdf-demo/``, sends them to the MinerU
API using the high-accuracy **vlm** model, and saves the Markdown output
alongside the original files.

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
    """Convert each PDF individually and save the Markdown output."""

    @pytest.fixture(autouse=True)
    def _setup_output_dir(self):
        """Ensure the output directory exists."""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def test_convert_and_save_each_pdf(self, client, pdf_files):
        """Convert each PDF one-by-one and save markdown to pdf-demo-output/."""
        succeeded = []
        failed = []

        for pdf_path in pdf_files:
            stem = pdf_path.stem
            print(f"\n{'─'*60}")
            print(f"  Converting: {pdf_path.name}")
            print(f"  Model:      {MODEL} (high-accuracy)")
            print(f"{'─'*60}")

            try:
                result = client.extract(
                    str(pdf_path),
                    model=MODEL,
                    timeout=SINGLE_TIMEOUT,
                )

                assert result.state == "done", (
                    f"[{pdf_path.name}] state={result.state}, "
                    f"err_code={result.err_code}, error={result.error}"
                )
                assert result.markdown is not None

                # Save markdown
                md_path = OUTPUT_DIR / f"{stem}.md"
                result.save_markdown(str(md_path), with_images=True)

                md_size = md_path.stat().st_size
                print(f"  ✅ Saved: {md_path.name}  ({md_size:,} bytes)")
                if result.images:
                    print(f"     Images: {len(result.images)} extracted")
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
        print(f"  Output:    {OUTPUT_DIR}")
        print(f"{'═'*60}\n")

        assert len(failed) == 0, f"{len(failed)} file(s) failed: {failed}"

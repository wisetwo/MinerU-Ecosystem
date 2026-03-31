import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import type { ContentListItem } from '../types';
import { TYPE_COLORS, TYPE_LABELS } from '../types';

// Configure PDF.js worker — use local file bundled by Vite instead of CDN
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString();

interface Props {
  pdfFile: File | null;
  contentList: ContentListItem[];
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  onTotalPagesChange: (total: number) => void;
  selectedElementIndex: number | null;
  onElementClick: (index: number) => void;
  showBboxOverlay: boolean;
  /** When set, scroll to this page (1-indexed). Cleared after scrolling. */
  scrollToPage?: number;
}

/** Natural (unscaled) page dimensions — set once after PDF load, never changes */
interface NaturalPageSize {
  width: number;
  height: number;
}

export default function PdfPanel({
  pdfFile,
  contentList,
  currentPage,
  totalPages,
  onPageChange,
  onTotalPagesChange,
  selectedElementIndex,
  onElementClick,
  showBboxOverlay,
  scrollToPage,
}: Props) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const canvasRefs = useRef<Map<number, HTMLCanvasElement>>(new Map());

  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [scale, setScale] = useState<number | null>(null); // null = not yet computed
  // Natural page sizes — computed once on load, never mutated afterwards
  const [naturalSizes, setNaturalSizes] = useState<Map<number, NaturalPageSize>>(new Map());
  const [renderedPages, setRenderedPages] = useState<Set<number>>(new Set());

  // Refs for tracking render / scroll state without triggering re-renders
  const renderingRef = useRef<Set<number>>(new Set());
  const renderedScaleRef = useRef<Map<number, number>>(new Map());
  const programmaticScrollRef = useRef(false);
  const currentPageRef = useRef(currentPage);
  currentPageRef.current = currentPage;

  // Effective scale for rendering (fallback to 1 only used before anything renders)
  const effectiveScale = scale ?? 1.0;

  // ─── Load PDF document ───────────────────────────────────────────────
  useEffect(() => {
    if (!pdfFile) {
      setPdfDoc(null);
      setNaturalSizes(new Map());
      setRenderedPages(new Set());
      setScale(null);
      renderedScaleRef.current.clear();
      return;
    }

    let cancelled = false;

    const loadPdf = async () => {
      const arrayBuffer = await pdfFile.arrayBuffer();
      const doc = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
      if (cancelled) return;

      setPdfDoc(doc);
      onTotalPagesChange(doc.numPages);

      // Compute natural sizes once
      const sizes = new Map<number, NaturalPageSize>();
      for (let i = 1; i <= doc.numPages; i++) {
        const page = await doc.getPage(i);
        const vp = page.getViewport({ scale: 1.0 });
        sizes.set(i, { width: vp.width, height: vp.height });
      }
      if (cancelled) return;
      setNaturalSizes(sizes);

      // Compute initial fit-to-width scale right here, before any render
      const container = scrollContainerRef.current;
      const firstPage = sizes.get(1);
      if (container && firstPage) {
        const containerWidth = container.clientWidth - 48;
        const fitScale = Math.min(containerWidth / firstPage.width, 2.0);
        setScale(fitScale);
      } else {
        setScale(1.0);
      }
    };

    loadPdf().catch(console.error);
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfFile]);

  // ─── Render a single page onto its canvas ────────────────────────────
  const renderPage = useCallback(
    async (pageNum: number) => {
      if (!pdfDoc || scale === null) return;
      // Skip if already rendering or already rendered at current scale
      if (renderingRef.current.has(pageNum)) return;
      if (renderedScaleRef.current.get(pageNum) === scale) return;

      const canvas = canvasRefs.current.get(pageNum);
      if (!canvas) return;

      renderingRef.current.add(pageNum);

      try {
        const page = await pdfDoc.getPage(pageNum);
        const viewport = page.getViewport({ scale });
        const ctx = canvas.getContext('2d')!;

        const dpr = window.devicePixelRatio || 1;
        canvas.width = viewport.width * dpr;
        canvas.height = viewport.height * dpr;
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        ctx.scale(dpr, dpr);

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        await page.render({ canvasContext: ctx, viewport } as any).promise;

        renderedScaleRef.current.set(pageNum, scale);
        setRenderedPages((prev) => {
          if (prev.has(pageNum)) return prev;
          const next = new Set(prev);
          next.add(pageNum);
          return next;
        });
      } catch {
        // render cancelled or failed — ignore
      } finally {
        renderingRef.current.delete(pageNum);
      }
    },
    [pdfDoc, scale]
  );

  // ─── IntersectionObserver: lazy-render pages that enter viewport ─────
  useEffect(() => {
    if (!pdfDoc || scale === null || !scrollContainerRef.current || naturalSizes.size === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const pageNum = Number(entry.target.getAttribute('data-page'));
            if (pageNum) renderPage(pageNum);
          }
        }
      },
      {
        root: scrollContainerRef.current,
        rootMargin: '300px 0px',
        threshold: 0,
      }
    );

    pageRefs.current.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [pdfDoc, renderPage, naturalSizes, totalPages, scale]);

  // ─── Re-render when scale changes ───────────────────────────────────
  useEffect(() => {
    if (!pdfDoc || scale === null) return;
    // Clear rendered-at-scale tracking so pages get re-rendered at new scale
    renderedScaleRef.current.clear();
    setRenderedPages(new Set());

    // Re-render pages that currently have canvas elements
    canvasRefs.current.forEach((_, pageNum) => {
      renderPage(pageNum);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scale, pdfDoc]);

  // ─── Track current page from scroll position ────────────────────────
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || !pdfDoc) return;

    let ticking = false;
    const handleScroll = () => {
      if (ticking || programmaticScrollRef.current) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        const containerRect = container.getBoundingClientRect();
        const targetY = containerRect.top + containerRect.height * 0.3;

        let closestPage = currentPageRef.current;
        let closestDist = Infinity;

        pageRefs.current.forEach((el, pageNum) => {
          const rect = el.getBoundingClientRect();
          const pageMid = rect.top + rect.height / 2;
          const dist = Math.abs(pageMid - targetY);
          if (dist < closestDist) {
            closestDist = dist;
            closestPage = pageNum;
          }
        });

        if (closestPage !== currentPageRef.current) {
          onPageChange(closestPage);
        }
      });
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, [pdfDoc, onPageChange]);

  // ─── Scroll to page (from right panel click or toolbar) ─────────────
  const scrollToPageFn = useCallback((page: number) => {
    const pageEl = pageRefs.current.get(page);
    if (!pageEl) return;
    programmaticScrollRef.current = true;
    pageEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(() => { programmaticScrollRef.current = false; }, 800);
  }, []);

  useEffect(() => {
    if (scrollToPage) scrollToPageFn(scrollToPage);
  }, [scrollToPage, scrollToPageFn]);

  const handleGoToPage = useCallback((page: number) => {
    const clamped = Math.max(1, Math.min(page, totalPages));
    onPageChange(clamped);
    scrollToPageFn(clamped);
  }, [totalPages, onPageChange, scrollToPageFn]);

  // ─── BBox coordinate mapping ─────────────────────────────────────────
  const bboxToPixels = useCallback(
    (bbox: [number, number, number, number], pageNum: number) => {
      const nat = naturalSizes.get(pageNum);
      if (!nat) return { left: 0, top: 0, width: 0, height: 0 };

      const displayW = nat.width * effectiveScale;
      const displayH = nat.height * effectiveScale;
      const [x1, y1, x2, y2] = bbox;
      const scaleX = displayW / 1000;
      const scaleY = displayH / 1000;

      return {
        left: x1 * scaleX,
        top: y1 * scaleY,
        width: (x2 - x1) * scaleX,
        height: (y2 - y1) * scaleY,
      };
    },
    [naturalSizes, effectiveScale]
  );

  // ─── Memoised index lookup ───────────────────────────────────────────
  const contentIndexMap = useMemo(() => {
    const map = new Map<ContentListItem, number>();
    contentList.forEach((item, idx) => map.set(item, idx));
    return map;
  }, [contentList]);

  const getGlobalIndex = (item: ContentListItem) => contentIndexMap.get(item) ?? -1;

  // ─── Zoom handlers ──────────────────────────────────────────────────
  const handleZoomIn = () => setScale((s) => Math.min((s ?? 1) + 0.2, 3.0));
  const handleZoomOut = () => setScale((s) => Math.max((s ?? 1) - 0.2, 0.3));

  // ─── Build pages array ──────────────────────────────────────────────
  const pages = useMemo(() => Array.from({ length: totalPages }, (_, i) => i + 1), [totalPages]);

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50 shrink-0">
        <div className="text-sm font-medium text-gray-600">Original File</div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-600 min-w-[80px] text-center">
            {currentPage} / {totalPages}
          </span>
          <div className="w-px h-4 bg-gray-300 mx-1" />
          <button onClick={handleZoomOut} className="px-2 py-1 text-sm rounded hover:bg-gray-200">
            🔍−
          </button>
          <span className="text-xs text-gray-500 min-w-[40px] text-center">
            {Math.round(effectiveScale * 100)}%
          </span>
          <button onClick={handleZoomIn} className="px-2 py-1 text-sm rounded hover:bg-gray-200">
            🔍+
          </button>
          <div className="w-px h-4 bg-gray-300 mx-1" />
          <button
            onClick={() => handleGoToPage(currentPage - 1)}
            disabled={currentPage <= 1}
            className="px-2 py-1 text-sm rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ▲
          </button>
          <button
            onClick={() => handleGoToPage(currentPage + 1)}
            disabled={currentPage >= totalPages}
            className="px-2 py-1 text-sm rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ▼
          </button>
        </div>
      </div>

      {/* Scrollable PDF pages */}
      <div ref={scrollContainerRef} className="flex-1 overflow-auto bg-gray-100">
        {pdfFile ? (
          <div className="flex flex-col items-center py-4 gap-4">
            {pages.map((pageNum) => {
              const nat = naturalSizes.get(pageNum);
              const displayW = (nat?.width ?? 595) * effectiveScale;
              const displayH = (nat?.height ?? 842) * effectiveScale;

              // Elements on this page
              const pageElements = showBboxOverlay
                ? contentList.filter((item) => item.page_idx === pageNum - 1)
                : [];

              return (
                <div
                  key={pageNum}
                  ref={(el) => {
                    if (el) pageRefs.current.set(pageNum, el);
                    else pageRefs.current.delete(pageNum);
                  }}
                  data-page={pageNum}
                  className="relative shadow-lg bg-white"
                  style={{ width: displayW, height: displayH }}
                >
                  {/* Page number badge */}
                  <div className="absolute -top-0 left-0 bg-gray-700 text-white text-xs px-2 py-0.5 rounded-br z-10 opacity-70">
                    P{pageNum}
                  </div>

                  <canvas
                    ref={(el) => {
                      if (el) canvasRefs.current.set(pageNum, el);
                      else canvasRefs.current.delete(pageNum);
                    }}
                    className="block"
                  />

                  {/* BBox overlays for this page */}
                  {renderedPages.has(pageNum) &&
                    pageElements.map((item, i) => {
                      const globalIdx = getGlobalIndex(item);
                      const pos = bboxToPixels(item.bbox, pageNum);
                      const color = TYPE_COLORS[item.type] || '#6B7280';
                      const isSelected = selectedElementIndex === globalIdx;

                      return (
                        <div
                          key={i}
                          className="bbox-overlay"
                          style={{
                            left: pos.left,
                            top: pos.top,
                            width: pos.width,
                            height: pos.height,
                            border: `${isSelected ? 2 : 1}px solid ${color}`,
                            backgroundColor: isSelected ? `${color}25` : `${color}10`,
                          }}
                          onClick={() => onElementClick(globalIdx)}
                          title={`${TYPE_LABELS[item.type] || item.type}: ${(item.text || item.img_path || '').slice(0, 60)}`}
                        >
                          <span
                            className="bbox-label"
                            style={{ backgroundColor: color, opacity: isSelected ? 1 : 0.7 }}
                          >
                            {TYPE_LABELS[item.type] || item.type}
                          </span>
                        </div>
                      );
                    })}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400">
            <div className="text-center">
              <div className="text-4xl mb-3">📄</div>
              <div>No PDF loaded</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

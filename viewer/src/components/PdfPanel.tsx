import { useEffect, useRef, useState, useCallback } from 'react';
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
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [scale, setScale] = useState(1.0);
  const [pageSize, setPageSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const renderTaskRef = useRef<ReturnType<pdfjsLib.PDFPageProxy['render']> | null>(null);

  // Load PDF document
  useEffect(() => {
    if (!pdfFile) {
      setPdfDoc(null);
      return;
    }

    const loadPdf = async () => {
      const arrayBuffer = await pdfFile.arrayBuffer();
      const doc = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
      setPdfDoc(doc);
      onTotalPagesChange(doc.numPages);
      if (currentPage < 1) onPageChange(1);
    };

    loadPdf().catch(console.error);
  }, [pdfFile]);

  // Render current page
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current) return;

    const renderPage = async () => {
      // Cancel previous render
      if (renderTaskRef.current) {
        try {
          renderTaskRef.current.cancel();
        } catch { /* ignore */ }
      }

      const page = await pdfDoc.getPage(currentPage);
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current!;
      const ctx = canvas.getContext('2d')!;

      const dpr = window.devicePixelRatio || 1;
      canvas.width = viewport.width * dpr;
      canvas.height = viewport.height * dpr;
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      ctx.scale(dpr, dpr);

      setPageSize({ width: viewport.width, height: viewport.height });

      const renderTask = page.render({
        canvasContext: ctx,
        viewport,
      });
      renderTaskRef.current = renderTask;

      try {
        await renderTask.promise;
      } catch {
        // render cancelled
      }
    };

    renderPage().catch(console.error);
  }, [pdfDoc, currentPage, scale]);

  // Auto-fit scale
  useEffect(() => {
    if (!pdfDoc || !containerRef.current) return;

    const fitScale = async () => {
      const page = await pdfDoc.getPage(currentPage);
      const viewport = page.getViewport({ scale: 1.0 });
      const containerWidth = containerRef.current!.clientWidth - 40;
      const newScale = containerWidth / viewport.width;
      setScale(Math.min(newScale, 2.0));
    };

    fitScale().catch(console.error);
  }, [pdfDoc, currentPage]);

  // Get elements for current page (0-indexed page_idx)
  const pageElements = contentList.filter((item) => item.page_idx === currentPage - 1);

  // Convert bbox from content_list coords (1000-based) to pixel coords
  const bboxToPixels = useCallback(
    (bbox: [number, number, number, number]) => {
      if (!pageSize.width || !pageSize.height) return { left: 0, top: 0, width: 0, height: 0 };
      // content_list bbox coords are based on a 1000x1000 (approximately) normalized coordinate
      // Actually they seem to be in the original PDF coordinate space
      // We need to figure out the coordinate system
      // Looking at the data: bbox values go up to ~1000, and PDF pages are typically ~595x842 pts
      // The bbox seems to be in a coordinate system where the page is normalized to ~1000 width
      // Let's calculate based on scale factor
      const [x1, y1, x2, y2] = bbox;
      const pdfPageWidth = pageSize.width / scale;
      const pdfPageHeight = pageSize.height / scale;
      
      // Assume bbox is in original PDF points (from the API, coords are proportional to page)
      // Looking at content_list: max x is ~915, max y is ~952 for a page
      // This matches typical PDF coordinates for A4: ~595 pt wide -> scaled to ~1000? No.
      // Actually examining the data more carefully: the bbox [92, 826, 865, 841] 
      // These seem to be in a coordinate system where page width ≈ 1000
      const scaleX = (pageSize.width) / 1000;
      const scaleY = (pageSize.height) / 1000;

      return {
        left: x1 * scaleX,
        top: y1 * scaleY,
        width: (x2 - x1) * scaleX,
        height: (y2 - y1) * scaleY,
      };
    },
    [pageSize, scale]
  );

  // Find global index of element in contentList
  const getGlobalIndex = (item: ContentListItem) => contentList.indexOf(item);

  const handleZoomIn = () => setScale((s) => Math.min(s + 0.2, 3.0));
  const handleZoomOut = () => setScale((s) => Math.max(s - 0.2, 0.3));
  const handlePrevPage = () => onPageChange(Math.max(1, currentPage - 1));
  const handleNextPage = () => onPageChange(Math.min(totalPages, currentPage + 1));

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50 shrink-0">
        <div className="text-sm font-medium text-gray-600">Original File</div>
        <div className="flex items-center gap-2">
          <button
            onClick={handlePrevPage}
            disabled={currentPage <= 1}
            className="px-2 py-1 text-sm rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ◀
          </button>
          <span className="text-sm text-gray-600 min-w-[80px] text-center">
            {currentPage} / {totalPages}
          </span>
          <button
            onClick={handleNextPage}
            disabled={currentPage >= totalPages}
            className="px-2 py-1 text-sm rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ▶
          </button>
          <div className="w-px h-4 bg-gray-300 mx-1" />
          <button onClick={handleZoomOut} className="px-2 py-1 text-sm rounded hover:bg-gray-200">
            🔍−
          </button>
          <span className="text-xs text-gray-500 min-w-[40px] text-center">
            {Math.round(scale * 100)}%
          </span>
          <button onClick={handleZoomIn} className="px-2 py-1 text-sm rounded hover:bg-gray-200">
            🔍+
          </button>
        </div>
      </div>

      {/* PDF Canvas */}
      <div ref={containerRef} className="flex-1 overflow-auto p-4 bg-gray-100 flex justify-center">
        {pdfFile ? (
          <div className="relative inline-block shadow-lg">
            <canvas ref={canvasRef} className="block" />
            {/* BBox overlays */}
            {showBboxOverlay &&
              pageElements.map((item, i) => {
                const globalIdx = getGlobalIndex(item);
                const pos = bboxToPixels(item.bbox);
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
                      backgroundColor: isSelected
                        ? `${color}25`
                        : `${color}10`,
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

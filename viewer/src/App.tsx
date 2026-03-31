import { useState, useCallback, useEffect } from 'react';
import PdfPanel from './components/PdfPanel';
import ContentPanel from './components/ContentPanel';
import FolderLoader from './components/FolderLoader';
import type { MinerUData, ContentListItem } from './types';

declare const __DEMO_DATA_ENABLED__: boolean;

/** Auto-load data from dev server's /__data__/ endpoint */
async function loadDemoData(): Promise<MinerUData | null> {
  try {
    // Fetch manifest (list of all files)
    const manifestRes = await fetch('/__data__/__manifest__.json');
    if (!manifestRes.ok) return null;
    const files: string[] = await manifestRes.json();

    const data: MinerUData = {
      contentList: [],
      markdown: '',
      layoutJson: null,
      images: new Map(),
      pdfFile: null,
      basePath: '(dev auto-load)',
    };

    // Load content_list.json
    if (files.includes('content_list.json')) {
      const res = await fetch('/__data__/content_list.json');
      if (res.ok) data.contentList = (await res.json()) as ContentListItem[];
    }

    // Load top-level markdown
    const mdFile = files.find((f) => f.endsWith('.md') && !f.includes('/'));
    if (mdFile) {
      const res = await fetch(`/__data__/${encodeURIComponent(mdFile)}`);
      if (res.ok) data.markdown = await res.text();
    }

    // Fallback: raw/full.md
    if (!data.markdown && files.includes('raw/full.md')) {
      const res = await fetch('/__data__/raw/full.md');
      if (res.ok) data.markdown = await res.text();
    }

    // Load layout.json
    if (files.includes('raw/layout.json')) {
      const res = await fetch('/__data__/raw/layout.json');
      if (res.ok) data.layoutJson = await res.json();
    }

    // Load images
    for (const f of files) {
      if (f.startsWith('images/') && /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(f)) {
        data.images.set(f, `/__data__/${encodeURIComponent(f)}`);
      }
      if (f.startsWith('raw/images/') && /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(f)) {
        const normalizedPath = f.replace('raw/', '');
        if (!data.images.has(normalizedPath)) {
          data.images.set(normalizedPath, `/__data__/${encodeURIComponent(f)}`);
        }
      }
    }

    // Load PDF
    const pdfFile = files.find((f) => f.endsWith('.pdf'));
    if (pdfFile) {
      const res = await fetch(`/__data__/${encodeURIComponent(pdfFile)}`);
      if (res.ok) {
        const blob = await res.blob();
        data.pdfFile = new File([blob], pdfFile.split('/').pop() || 'document.pdf', {
          type: 'application/pdf',
        });
      }
    }

    return data;
  } catch (err) {
    console.warn('Failed to auto-load demo data:', err);
    return null;
  }
}

function App() {
  const [data, setData] = useState<MinerUData | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [selectedElementIndex, setSelectedElementIndex] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<'markdown' | 'json'>('markdown');
  const [showBbox, setShowBbox] = useState(true);
  const [splitPos] = useState(50); // percentage
  const [autoLoading, setAutoLoading] = useState(false);
  const [scrollToPage, setScrollToPage] = useState<number | undefined>(undefined);

  // Auto-load demo data in dev mode
  useEffect(() => {
    if (typeof __DEMO_DATA_ENABLED__ !== 'undefined' && __DEMO_DATA_ENABLED__) {
      setAutoLoading(true);
      loadDemoData().then((d) => {
        if (d) setData(d);
        setAutoLoading(false);
      });
    }
  }, []);

  const handleDataLoaded = useCallback((newData: MinerUData) => {
    setData(newData);
    setCurrentPage(1);
    setSelectedElementIndex(null);
  }, []);

  const handleElementClickFromPdf = useCallback(
    (index: number) => {
      setSelectedElementIndex(index);
      // Optionally switch to markdown tab to show the highlight
      if (activeTab === 'json') setActiveTab('markdown');
    },
    [activeTab]
  );

  const handleElementClickFromContent = useCallback(
    (index: number) => {
      if (!data) return;
      setSelectedElementIndex(index);
      // Navigate PDF to the page containing this element
      const item = data.contentList[index];
      if (item) {
        const targetPage = item.page_idx + 1;
        setCurrentPage(targetPage);
        // Trigger scroll in PDF panel
        setScrollToPage(targetPage);
        // Clear after a tick so it can be triggered again for same page
        setTimeout(() => setScrollToPage(undefined), 100);
      }
    },
    [data]
  );

  const handleReload = useCallback(() => {
    // Clean up object URLs
    if (data) {
      data.images.forEach((url) => URL.revokeObjectURL(url));
    }
    setData(null);
    setCurrentPage(1);
    setTotalPages(0);
    setSelectedElementIndex(null);
  }, [data]);

  // Auto-loading state
  if (autoLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-gradient-to-br from-blue-50 to-indigo-50">
        <div className="text-5xl mb-4 animate-spin">⏳</div>
        <div className="text-lg text-gray-600">Auto-loading demo data...</div>
      </div>
    );
  }

  // If no data loaded, show the folder loader
  if (!data) {
    return <FolderLoader onDataLoaded={handleDataLoaded} />;
  }

  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="flex items-center px-4 py-2 bg-white border-b border-gray-200 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold text-gray-800">📄 MinerU Viewer</span>
          <span className="text-sm text-gray-400">—</span>
          <span className="text-sm text-gray-500 truncate max-w-xs">{data.basePath}</span>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
            <input
              type="checkbox"
              checked={showBbox}
              onChange={(e) => setShowBbox(e.target.checked)}
              className="rounded"
            />
            Show BBox
          </label>
          <button
            onClick={handleReload}
            className="px-3 py-1 text-sm border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
          >
            🔄 Open Another
          </button>
        </div>
      </div>

      {/* Split panels */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: PDF */}
        <div style={{ width: `${splitPos}%` }} className="border-r border-gray-200">
          <PdfPanel
            pdfFile={data.pdfFile}
            contentList={data.contentList}
            currentPage={currentPage}
            totalPages={totalPages}
            onPageChange={setCurrentPage}
            onTotalPagesChange={setTotalPages}
            selectedElementIndex={selectedElementIndex}
            onElementClick={handleElementClickFromPdf}
            showBboxOverlay={showBbox}
            scrollToPage={scrollToPage}
          />
        </div>

        {/* Right: Content */}
        <div style={{ width: `${100 - splitPos}%` }}>
          <ContentPanel
            markdown={data.markdown}
            contentList={data.contentList}
            layoutJson={data.layoutJson}
            images={data.images}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            selectedElementIndex={selectedElementIndex}
            onElementClick={handleElementClickFromContent}
            currentPage={currentPage}
          />
        </div>
      </div>
    </div>
  );
}

export default App;

import { useState, useCallback } from 'react';
import PdfPanel from './components/PdfPanel';
import ContentPanel from './components/ContentPanel';
import FolderLoader from './components/FolderLoader';
import type { MinerUData } from './types';

function App() {
  const [data, setData] = useState<MinerUData | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [selectedElementIndex, setSelectedElementIndex] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<'markdown' | 'json'>('markdown');
  const [showBbox, setShowBbox] = useState(true);
  const [splitPos] = useState(50); // percentage

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
        if (targetPage !== currentPage) {
          setCurrentPage(targetPage);
        }
      }
    },
    [data, currentPage]
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

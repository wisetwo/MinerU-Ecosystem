import { useEffect, useRef, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import type { ContentListItem } from '../types';
import { TYPE_COLORS } from '../types';

interface Props {
  markdown: string;
  contentList: ContentListItem[];
  layoutJson: object | null;
  images: Map<string, string>;
  activeTab: 'markdown' | 'json';
  onTabChange: (tab: 'markdown' | 'json') => void;
  selectedElementIndex: number | null;
  onElementClick: (index: number) => void;
  currentPage: number;
}

export default function ContentPanel({
  markdown,
  contentList,
  layoutJson,
  images,
  activeTab,
  onTabChange,
  selectedElementIndex,
  onElementClick,
  currentPage,
}: Props) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const elementRefs = useRef<Map<number, HTMLElement>>(new Map());

  // Scroll to selected element
  useEffect(() => {
    if (selectedElementIndex === null) return;
    const el = elementRefs.current.get(selectedElementIndex);
    if (el && scrollContainerRef.current) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.remove('highlight-flash');
      // Force reflow
      void el.offsetWidth;
      el.classList.add('highlight-flash');
    }
  }, [selectedElementIndex]);

  // Process markdown to replace local image paths with object URLs
  const processedMarkdown = useMemo(() => {
    let md = markdown;
    images.forEach((objectUrl, imgPath) => {
      // Replace both formats: ![](images/xxx.jpg) and ![](xxx.jpg)
      md = md.replaceAll(`](${imgPath})`, `](${objectUrl})`);
      // Also try without images/ prefix
      if (imgPath.startsWith('images/')) {
        md = md.replaceAll(`](${imgPath.slice(7)})`, `](${objectUrl})`);
      }
    });
    return md;
  }, [markdown, images]);

  // Render content list as structured markdown segments
  const renderContentListMarkdown = () => {
    return contentList.map((item, index) => {
      const isSelected = selectedElementIndex === index;
      const isCurrentPage = item.page_idx === currentPage - 1;
      const color = TYPE_COLORS[item.type] || '#6B7280';

      return (
        <div
          key={index}
          ref={(el) => {
            if (el) elementRefs.current.set(index, el);
          }}
          className={`px-3 py-1 rounded-sm cursor-pointer transition-all duration-150 border-l-3 ${
            isSelected
              ? 'bg-blue-50 border-blue-500'
              : isCurrentPage
                ? 'border-transparent hover:bg-gray-50'
                : 'border-transparent opacity-60 hover:opacity-100 hover:bg-gray-50'
          }`}
          style={isSelected ? { borderLeftColor: color } : undefined}
          onClick={() => onElementClick(index)}
        >
          {renderContentItem(item, images)}
        </div>
      );
    });
  };

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Tab bar */}
      <div className="flex items-center px-4 py-2 border-b border-gray-200 bg-gray-50 shrink-0">
        <div className="flex gap-1 bg-gray-200 rounded-md p-0.5">
          <button
            onClick={() => onTabChange('markdown')}
            className={`px-3 py-1 text-sm rounded-md transition-all ${
              activeTab === 'markdown'
                ? 'bg-white text-blue-600 font-medium shadow-sm'
                : 'text-gray-600 hover:text-gray-800'
            }`}
          >
            Markdown
          </button>
          <button
            onClick={() => onTabChange('json')}
            className={`px-3 py-1 text-sm rounded-md transition-all ${
              activeTab === 'json'
                ? 'bg-white text-blue-600 font-medium shadow-sm'
                : 'text-gray-600 hover:text-gray-800'
            }`}
          >
            JSON
          </button>
        </div>
        <div className="ml-auto text-xs text-gray-400">
          {contentList.length} elements
        </div>
      </div>

      {/* Content */}
      <div ref={scrollContainerRef} className="flex-1 overflow-auto p-5">
        {activeTab === 'markdown' ? (
          <div className="markdown-body max-w-none">
            {contentList.length > 0 ? (
              renderContentListMarkdown()
            ) : processedMarkdown ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
                {processedMarkdown}
              </ReactMarkdown>
            ) : (
              <div className="text-gray-400 text-center py-12">No content loaded</div>
            )}
          </div>
        ) : (
          <div className="json-viewer">
            {layoutJson ? (
              <pre className="text-xs leading-5 overflow-auto">
                {JSON.stringify(layoutJson, null, 2)}
              </pre>
            ) : contentList.length > 0 ? (
              <pre className="text-xs leading-5 overflow-auto">
                {JSON.stringify(contentList, null, 2)}
              </pre>
            ) : (
              <div className="text-gray-400 text-center py-12">No JSON data loaded</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Render a single content item as appropriate markdown/HTML */
function renderContentItem(item: ContentListItem, images: Map<string, string>) {
  switch (item.type) {
    case 'text': {
      if (item.text_level === 1) {
        return <h1 className="text-xl font-bold my-2">{item.text}</h1>;
      }
      if (item.text_level === 2) {
        return <h2 className="text-lg font-semibold my-1.5">{item.text}</h2>;
      }
      return <p className="my-1">{item.text}</p>;
    }
    case 'header':
      return <div className="text-xs text-gray-400 italic">{item.text || '(header)'}</div>;
    case 'page_number':
      return <div className="text-xs text-gray-400 text-right">Page {item.text}</div>;
    case 'page_footnote':
      return <div className="text-xs text-gray-500 border-t border-gray-200 pt-1 mt-1">{item.text}</div>;
    case 'image': {
      const imgSrc = item.img_path ? (images.get(item.img_path) || item.img_path) : '';
      return (
        <div className="my-2">
          {imgSrc && <img src={imgSrc} alt="" className="max-w-full rounded" />}
          {item.image_caption?.map((cap, i) => (
            <div key={i} className="text-xs text-gray-500 mt-1 text-center">{cap}</div>
          ))}
        </div>
      );
    }
    case 'table': {
      const imgSrc = item.img_path ? (images.get(item.img_path) || item.img_path) : '';
      return (
        <div className="my-2">
          {item.table_caption?.map((cap, i) => (
            <div key={i} className="text-sm font-medium mb-1">{cap}</div>
          ))}
          {item.table_body ? (
            <div
              className="overflow-auto text-xs"
              dangerouslySetInnerHTML={{ __html: item.table_body }}
            />
          ) : imgSrc ? (
            <img src={imgSrc} alt="table" className="max-w-full rounded" />
          ) : null}
        </div>
      );
    }
    case 'list':
      return (
        <ul className="list-disc pl-5 my-1">
          {item.list_items?.map((li, i) => (
            <li key={i} className="text-sm my-0.5">{li}</li>
          ))}
        </ul>
      );
    default:
      return <p className="my-1 text-sm">{item.text || JSON.stringify(item)}</p>;
  }
}

// ── MinerU Output Data Types ──

/** content_list.json element */
export interface ContentListItem {
  type: 'text' | 'image' | 'table' | 'header' | 'page_number' | 'page_footnote' | 'list';
  text?: string;
  text_level?: number;
  img_path?: string;
  image_caption?: string[];
  image_footnote?: string[];
  table_caption?: string[];
  table_footnote?: string[];
  table_body?: string;
  sub_type?: string;
  list_items?: string[];
  bbox: [number, number, number, number]; // [x1, y1, x2, y2] in 1000-based coords
  page_idx: number;
}

/** layout.json page structure */
export interface LayoutPage {
  para_blocks: LayoutBlock[];
  discarded_blocks: LayoutBlock[];
  page_size: [number, number];
}

export interface LayoutBlock {
  bbox: [number, number, number, number];
  type: string;
  angle: number;
  index: number;
  lines: LayoutLine[];
}

export interface LayoutLine {
  bbox: [number, number, number, number];
  spans: LayoutSpan[];
}

export interface LayoutSpan {
  bbox: [number, number, number, number];
  type: string;
  content: string;
  score: number;
}

/** Viewer state */
export interface MinerUData {
  contentList: ContentListItem[];
  markdown: string;
  layoutJson: { pdf_info: LayoutPage[] } | null;
  images: Map<string, string>; // img_path -> object URL
  pdfFile: File | null;
  basePath: string;
}

/** Element type color mapping */
export const TYPE_COLORS: Record<string, string> = {
  text: '#3B82F6',       // blue
  title: '#3B82F6',
  header: '#8B5CF6',     // purple
  image: '#10B981',      // green
  table: '#F59E0B',      // amber
  page_number: '#6B7280', // gray
  page_footnote: '#6B7280',
  list: '#EC4899',       // pink
  page_header: '#8B5CF6',
};

export const TYPE_LABELS: Record<string, string> = {
  text: 'Text',
  title: 'Title',
  header: 'Header',
  image: 'Image',
  table: 'Table',
  page_number: 'Page#',
  page_footnote: 'Footnote',
  list: 'List',
  page_header: 'Header',
};

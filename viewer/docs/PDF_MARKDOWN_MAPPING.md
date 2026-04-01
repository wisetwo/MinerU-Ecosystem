# PDF 元素高亮与 Markdown 元素双向对应机制

## 概述

MinerU Viewer 实现了 PDF 文档与解析后 Markdown 内容之间的**双向联动高亮**功能：
- 点击左侧 PDF 上的元素框，右侧对应的 Markdown 内容会高亮并滚动到视图
- 点击右侧的 Markdown 内容块，左侧 PDF 会跳转到对应页面并高亮该元素

---

## 核心数据结构

### 1. `ContentListItem` - 元素对应的桥梁

```typescript
// types.ts
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
  bbox: [number, number, number, number];  // ⭐ 关键：PDF 中的边界框坐标
  page_idx: number;                         // ⭐ 关键：PDF 页码索引
}
```

**核心字段说明：**

| 字段 | 说明 |
|------|------|
| `bbox` | 边界框坐标 `[x1, y1, x2, y2]`，基于 1000×1000 的归一化坐标系 |
| `page_idx` | 元素所在的 PDF 页面索引（从 0 开始） |
| `type` | 元素类型，用于渲染和着色 |
| `text` / `img_path` / `table_body` 等 | 元素的实际内容 |

### 2. `MinerUData` - 应用全局状态

```typescript
export interface MinerUData {
  contentList: ContentListItem[];  // ⭐ 核心：元素列表（双向对应的数据源）
  markdown: string;                // 原始 markdown 文本
  layoutJson: { pdf_info: LayoutPage[] } | null;
  images: Map<string, string>;     // 图片路径 -> Object URL
  pdfFile: File | null;
  basePath: string;
}
```

---

## 双向对应机制详解

### 数据流架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              App.tsx (状态管理)                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  selectedElementIndex: number | null  ← 当前选中元素的全局索引        │    │
│  │  currentPage: number                  ← 当前 PDF 页码               │    │
│  │  contentList: ContentListItem[]       ← 所有元素数据（共享）          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
              │                                        │
              │ props                                  │ props
              ▼                                        ▼
┌─────────────────────────────┐          ┌─────────────────────────────────────┐
│       PdfPanel.tsx          │          │         ContentPanel.tsx            │
│  ┌───────────────────────┐  │          │  ┌───────────────────────────────┐  │
│  │ 遍历 contentList       │  │          │  │ 遍历 contentList               │  │
│  │ 按 page_idx 筛选当前页  │  │          │  │ 使用 index 作为元素标识        │  │
│  │ 用 bbox 绘制高亮框     │  │          │  │ 渲染为结构化 Markdown 块       │  │
│  └───────────────────────┘  │          │  └───────────────────────────────┘  │
│                             │          │                                     │
│  onClick → globalIndex ─────┼──────────┼───→ selectedElementIndex           │
│                             │          │                                     │
│  ← selectedElementIndex ────┼──────────┼─── onClick → globalIndex           │
│  ← scrollToPage ────────────┼──────────┼─── item.page_idx + 1               │
└─────────────────────────────┘          └─────────────────────────────────────┘
```

---

## 核心实现代码解析

### 1. 状态管理（App.tsx）

```typescript
// 核心状态
const [selectedElementIndex, setSelectedElementIndex] = useState<number | null>(null);
const [currentPage, setCurrentPage] = useState(1);
const [scrollToPage, setScrollToPage] = useState<number | undefined>(undefined);

// PDF → Markdown 方向：点击 PDF 元素
const handleElementClickFromPdf = useCallback((index: number) => {
  setSelectedElementIndex(index);  // 设置选中索引
  if (activeTab === 'json') setActiveTab('markdown');  // 切换到 Markdown 标签
}, [activeTab]);

// Markdown → PDF 方向：点击 Markdown 元素
const handleElementClickFromContent = useCallback((index: number) => {
  if (!data) return;
  setSelectedElementIndex(index);  // 设置选中索引
  
  // 关键：通过 contentList[index] 获取元素的 page_idx
  const item = data.contentList[index];
  if (item) {
    const targetPage = item.page_idx + 1;  // page_idx 从 0 开始，页码从 1 开始
    setCurrentPage(targetPage);
    setScrollToPage(targetPage);           // 触发 PDF 滚动
    setTimeout(() => setScrollToPage(undefined), 100);
  }
}, [data]);
```

### 2. PDF 侧高亮实现（PdfPanel.tsx）

#### 2.1 BBox 坐标转换

```typescript
// 将 1000-based 归一化坐标转换为像素坐标
const bboxToPixels = useCallback(
  (bbox: [number, number, number, number], pageNum: number) => {
    const nat = naturalSizes.get(pageNum);  // 获取页面原始尺寸
    if (!nat) return { left: 0, top: 0, width: 0, height: 0 };

    const displayW = nat.width * effectiveScale;   // 当前显示宽度
    const displayH = nat.height * effectiveScale;  // 当前显示高度
    const [x1, y1, x2, y2] = bbox;
    
    // 坐标转换公式：(bbox坐标 / 1000) * 实际显示尺寸
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
```

#### 2.2 渲染高亮框

```typescript
// 筛选当前页面的元素
const pageElements = showBboxOverlay
  ? contentList.filter((item) => item.page_idx === pageNum - 1)
  : [];

// 渲染每个元素的高亮框
{pageElements.map((item, i) => {
  const globalIdx = getGlobalIndex(item);        // 获取全局索引
  const pos = bboxToPixels(item.bbox, pageNum);  // 计算像素位置
  const color = TYPE_COLORS[item.type] || '#6B7280';
  const isSelected = selectedElementIndex === globalIdx;  // 是否选中

  return (
    <div
      className="bbox-overlay"
      style={{
        left: pos.left,
        top: pos.top,
        width: pos.width,
        height: pos.height,
        border: `${isSelected ? 2 : 1}px solid ${color}`,
        backgroundColor: isSelected ? `${color}25` : `${color}10`,
      }}
      onClick={() => onElementClick(globalIdx)}  // ⭐ 点击时传递全局索引
    />
  );
})}
```

#### 2.3 索引映射

```typescript
// 建立 ContentListItem -> globalIndex 的映射
const contentIndexMap = useMemo(() => {
  const map = new Map<ContentListItem, number>();
  contentList.forEach((item, idx) => map.set(item, idx));
  return map;
}, [contentList]);

const getGlobalIndex = (item: ContentListItem) => contentIndexMap.get(item) ?? -1;
```

### 3. Markdown 侧高亮实现（ContentPanel.tsx）

#### 3.1 渲染元素列表

```typescript
const renderContentListMarkdown = () => {
  return contentList.map((item, index) => {  // ⭐ index 即为全局索引
    const isSelected = selectedElementIndex === index;
    const isCurrentPage = item.page_idx === currentPage - 1;
    const color = TYPE_COLORS[item.type] || '#6B7280';

    return (
      <div
        key={index}
        ref={(el) => {
          if (el) elementRefs.current.set(index, el);  // 存储 DOM 引用
        }}
        className={`... ${isSelected ? 'bg-blue-50 border-blue-500' : '...'}`}
        onClick={() => onElementClick(index)}  // ⭐ 点击时传递索引
      >
        {renderContentItem(item, images)}
      </div>
    );
  });
};
```

#### 3.2 选中时自动滚动

```typescript
// 当 selectedElementIndex 变化时，滚动到对应元素
useEffect(() => {
  if (selectedElementIndex === null) return;
  
  const el = elementRefs.current.get(selectedElementIndex);  // 获取 DOM 元素
  if (el && scrollContainerRef.current) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('highlight-flash');
    void el.offsetWidth;  // 强制重排
    el.classList.add('highlight-flash');  // 添加闪烁动画
  }
}, [selectedElementIndex]);
```

---

## 双向对应流程总结

### 流程 A：PDF → Markdown

```
1. 用户点击 PDF 上的高亮框
   ↓
2. PdfPanel 调用 onElementClick(globalIdx)
   ↓
3. App 执行 handleElementClickFromPdf(index)
   → setSelectedElementIndex(index)
   ↓
4. ContentPanel 收到新的 selectedElementIndex
   → elementRefs.current.get(index) 获取对应 DOM
   → scrollIntoView() 滚动到视图
   → 添加高亮样式
```

### 流程 B：Markdown → PDF

```
1. 用户点击 Markdown 内容块
   ↓
2. ContentPanel 调用 onElementClick(index)
   ↓
3. App 执行 handleElementClickFromContent(index)
   → setSelectedElementIndex(index)
   → 从 contentList[index] 获取 page_idx
   → setCurrentPage(page_idx + 1)
   → setScrollToPage(page_idx + 1)
   ↓
4. PdfPanel 收到新的 scrollToPage
   → scrollToPageFn(page) 滚动到目标页面
   ↓
5. PdfPanel 收到新的 selectedElementIndex
   → 对应的高亮框渲染为选中状态（加粗边框 + 深色背景）
```

---

## 关键数据文件

Viewer 依赖 MinerU 解析输出的以下文件：

| 文件 | 必要性 | 用途 |
|------|--------|------|
| `content_list.json` | **✅ 必须** | 双向高亮核心数据：`bbox`、`page_idx`、内容 |
| `*.pdf` | ✅ 必须 | 原始 PDF 文件，用于左侧渲染 |
| `images/` | 可选 | 图片资源目录 |
| `*.md` | 可选 | Markdown 文本（作为备用显示） |
| `raw/layout.json` | ⚠️ 可选 | 仅用于 JSON Tab 调试展示 |

### `content_list.json` vs `raw/layout.json` 对比

| 特性 | `content_list.json` | `raw/layout.json` |
|------|---------------------|-------------------|
| **数据结构** | 扁平数组 `[{}, {}, ...]` | 嵌套结构 `{pdf_info: [{para_blocks: [...]}]}` |
| **组织方式** | 按元素顺序排列 | 按**页面**组织，每页包含 block → lines → spans |
| **页码信息** | `page_idx` 字段 | 隐含在数组索引（`pdf_info[0]` = 第1页） |
| **坐标系** | 1000-based 归一化坐标 | 原始 PDF 像素坐标 |
| **文件大小** | 较小（~几十KB） | 较大（~几百KB） |
| **Viewer 用途** | **双向高亮 + Markdown 渲染** | 仅 JSON Tab 展示 |

**结论**：`content_list.json` 是核心功能必须的数据；`raw/layout.json` 仅供开发调试查看 MinerU 原始解析结构，不影响双向高亮功能。

### content_list.json 示例

```json
[
  {
    "type": "text",
    "text": "Introduction",
    "text_level": 1,
    "bbox": [72, 80, 540, 110],
    "page_idx": 0
  },
  {
    "type": "image",
    "img_path": "images/figure_1.png",
    "image_caption": ["Figure 1: System Architecture"],
    "bbox": [100, 200, 500, 450],
    "page_idx": 0
  },
  {
    "type": "table",
    "table_body": "<table>...</table>",
    "table_caption": ["Table 1: Performance Metrics"],
    "bbox": [72, 500, 540, 700],
    "page_idx": 1
  }
]
```

---

## 元素类型颜色映射

```typescript
export const TYPE_COLORS: Record<string, string> = {
  text: '#3B82F6',        // 蓝色
  title: '#3B82F6',       // 蓝色
  header: '#8B5CF6',      // 紫色
  image: '#10B981',       // 绿色
  table: '#F59E0B',       // 琥珀色
  page_number: '#6B7280', // 灰色
  page_footnote: '#6B7280', // 灰色
  list: '#EC4899',        // 粉色
  page_header: '#8B5CF6', // 紫色
};
```

---

## 技术要点总结

1. **统一索引**：使用 `contentList` 数组的索引作为全局唯一标识符，实现双向映射
2. **归一化坐标**：`bbox` 使用 1000-based 坐标系，适配任意缩放比例
3. **页面关联**：`page_idx` 字段关联 PDF 页面与元素
4. **状态提升**：`selectedElementIndex` 状态提升到 `App.tsx` 层级，实现跨组件同步
5. **DOM 引用**：通过 `useRef` + `Map` 存储元素 DOM 引用，支持程序化滚动
6. **懒加载**：PDF 页面使用 `IntersectionObserver` 实现按需渲染

# MinerU Document Viewer

一个交互式的 MinerU 文档解析结果查看器，支持 PDF 预览、bbox 标注、Markdown 渲染及左右联动高亮。

## 功能

- **PDF 预览** — 左侧渲染 PDF 原文件，支持翻页、缩放
- **BBox 标注** — 基于 `content_list.json` 在 PDF 上绘制元素识别框，按类型着色
- **Markdown/JSON 切换** — 右侧顶部 Tab 切换 Markdown 渲染 / JSON 原始数据
- **左右联动** — 点击左侧 bbox 高亮右侧对应 Markdown 段落；点击右侧段落自动翻页到对应 PDF 页面
- **文件夹加载** — 通过界面选择 MinerU 输出目录

## 快速开始

```bash
cd viewer
npm install
npm run dev
```

然后在浏览器中打开 `http://localhost:5173`，点击 **Open Output Folder** 选择 MinerU 的输出目录。

## 期望的文件夹结构

```
output_folder/
├── content_list.json        # 结构化内容列表
├── *.md                     # Markdown 输出
├── images/                  # 提取的图片
│   ├── img_0.jpg
│   └── ...
└── raw/                     # 原始 API 输出
    ├── layout.json          # 布局 JSON
    ├── full.md              # 完整 Markdown
    └── *.pdf                # 原始 PDF
```

## 技术栈

- React 18 + TypeScript
- Vite
- TailwindCSS v4
- pdfjs-dist（PDF 渲染）
- react-markdown + rehype-raw + remark-gfm（Markdown 渲染）

## 数据格式说明

### content_list.json

每个元素包含：
- `type`: text / image / table / header / page_number / page_footnote / list
- `text` / `img_path` / `table_body`: 内容
- `bbox`: `[x1, y1, x2, y2]` — 在 PDF 页面上的坐标（1000 基准坐标系）
- `page_idx`: 所在页面索引（0-based）

### layout.json

更底层的布局信息，包含每个 block/line/span 级别的 bbox 和识别置信度。

## 开发

```bash
npm run dev      # 开发模式
npm run build    # 生产构建
npm run preview  # 预览生产构建
```

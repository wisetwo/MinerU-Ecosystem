import { useCallback, useState, type DragEvent } from 'react';
import type { ContentListItem, MinerUData } from '../types';

interface Props {
  onDataLoaded: (data: MinerUData) => void;
}

export default function FolderLoader({ onDataLoaded }: Props) {
  const [isDragOver, setIsDragOver] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const handleFolderSelect = useCallback(async () => {
    try {
      setIsLoading(true);
      const dirHandle = await (window as unknown as { showDirectoryPicker: () => Promise<FileSystemDirectoryHandle> }).showDirectoryPicker();
      await loadFromDirectoryHandle(dirHandle, onDataLoaded);
    } catch (err) {
      if ((err as Error).name === 'AbortError') { setIsLoading(false); return; }
      console.error('Failed to load folder:', err);
    } finally {
      setIsLoading(false);
    }
  }, [onDataLoaded]);

  // Fallback: use input[webkitdirectory]
  const handleInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;

      setIsLoading(true);
      const fileMap = new Map<string, File>();
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const relativePath = file.webkitRelativePath || file.name;
        fileMap.set(relativePath, file);
      }

      await loadFromFileMap(fileMap, onDataLoaded);
      setIsLoading(false);
    },
    [onDataLoaded]
  );

  // Drag & drop support
  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    async (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);

      const items = e.dataTransfer.items;
      if (!items || items.length === 0) return;

      setIsLoading(true);
      try {
        // Try File System Access API first
        const firstItem = items[0];
        if ('getAsFileSystemHandle' in firstItem) {
          const handle = await (firstItem as unknown as { getAsFileSystemHandle: () => Promise<FileSystemDirectoryHandle> }).getAsFileSystemHandle();
          if (handle && handle.kind === 'directory') {
            await loadFromDirectoryHandle(handle as FileSystemDirectoryHandle, onDataLoaded);
            return;
          }
        }

        // Fallback: collect files from DataTransferItemList
        const files = new Map<string, File>();
        const entries: FileSystemEntry[] = [];
        for (let i = 0; i < items.length; i++) {
          const entry = items[i].webkitGetAsEntry?.();
          if (entry) entries.push(entry);
        }
        await collectEntriesRecursive(entries, '', files);
        if (files.size > 0) {
          await loadFromFileMap(files, onDataLoaded);
        }
      } catch (err) {
        console.error('Drop failed:', err);
      } finally {
        setIsLoading(false);
      }
    },
    [onDataLoaded]
  );

  return (
    <div
      className={`flex flex-col items-center justify-center h-full p-8 transition-colors ${
        isDragOver
          ? 'bg-blue-100 border-2 border-dashed border-blue-400'
          : 'bg-gradient-to-br from-blue-50 to-indigo-50'
      }`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isLoading ? (
        <div className="text-center">
          <div className="text-5xl mb-4 animate-spin">⏳</div>
          <div className="text-lg text-gray-600">Loading files...</div>
        </div>
      ) : (
      <div className="text-center max-w-lg">
        <div className="text-6xl mb-6">{isDragOver ? '📥' : '📂'}</div>
        <h1 className="text-2xl font-bold text-gray-800 mb-2">MinerU Document Viewer</h1>
        <p className="text-gray-500 mb-8">
          {isDragOver
            ? 'Drop the folder here to load...'
            : 'Select or drag a MinerU output folder to visualize PDF parsing results with interactive bbox overlays and markdown rendering.'}
        </p>

        <div className="flex flex-col gap-3 items-center">
          <button
            onClick={handleFolderSelect}
            className="px-6 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors shadow-md hover:shadow-lg"
          >
            📁 Open Output Folder
          </button>

          <div className="text-xs text-gray-400 flex items-center gap-2">
            <div className="h-px w-8 bg-gray-300" />
            or drag & drop a folder here
            <div className="h-px w-8 bg-gray-300" />
          </div>

          <label className="px-4 py-2 border border-gray-300 text-gray-600 rounded-lg text-sm cursor-pointer hover:bg-gray-50 transition-colors">
            Use file input (fallback)
            <input
              type="file"
              // @ts-expect-error webkitdirectory is not typed
              webkitdirectory=""
              directory=""
              multiple
              className="hidden"
              onChange={handleInputChange}
            />
          </label>
        </div>

        <div className="mt-8 p-4 bg-white/60 rounded-lg text-left text-xs text-gray-500">
          <div className="font-medium text-gray-700 mb-2">Expected folder structure:</div>
          <pre className="font-mono leading-relaxed">
{`output_folder/
├── content_list.json
├── *.md  (markdown)
├── images/
│   ├── img_0.jpg
│   └── ...
└── raw/
    ├── layout.json
    ├── full.md
    └── *.pdf  (original PDF)`}
          </pre>
        </div>
      </div>
      )}
    </div>
  );
}

async function loadFromDirectoryHandle(
  dirHandle: FileSystemDirectoryHandle,
  onDataLoaded: (data: MinerUData) => void
) {
  const data: MinerUData = {
    contentList: [],
    markdown: '',
    layoutJson: null,
    images: new Map(),
    pdfFile: null,
    basePath: dirHandle.name,
  };

  // Recursively collect all files
  const files = new Map<string, File>();
  await collectFiles(dirHandle, '', files);

  await processFiles(files, data);
  onDataLoaded(data);
}

async function collectFiles(
  dirHandle: FileSystemDirectoryHandle,
  prefix: string,
  result: Map<string, File>
) {
  for await (const [name, handle] of dirHandle.entries()) {
    const path = prefix ? `${prefix}/${name}` : name;
    if (handle.kind === 'file') {
      const file = await (handle as FileSystemFileHandle).getFile();
      result.set(path, file);
    } else if (handle.kind === 'directory') {
      await collectFiles(handle as FileSystemDirectoryHandle, path, result);
    }
  }
}

async function loadFromFileMap(
  fileMap: Map<string, File>,
  onDataLoaded: (data: MinerUData) => void
) {
  const data: MinerUData = {
    contentList: [],
    markdown: '',
    layoutJson: null,
    images: new Map(),
    pdfFile: null,
    basePath: '',
  };

  // Normalize paths: strip the top-level folder name
  const normalizedMap = new Map<string, File>();
  for (const [path, file] of fileMap) {
    // webkitRelativePath is like "foldername/subfolder/file.ext"
    const parts = path.split('/');
    if (parts.length > 1) {
      if (!data.basePath) data.basePath = parts[0];
      normalizedMap.set(parts.slice(1).join('/'), file);
    } else {
      normalizedMap.set(path, file);
    }
  }

  await processFiles(normalizedMap, data);
  onDataLoaded(data);
}

async function processFiles(files: Map<string, File>, data: MinerUData) {
  for (const [path, file] of files) {
    const lowerPath = path.toLowerCase();

    // content_list.json (top-level or raw/)
    if (lowerPath === 'content_list.json') {
      try {
        const text = await file.text();
        data.contentList = JSON.parse(text) as ContentListItem[];
      } catch (e) {
        console.warn('Failed to parse content_list.json:', e);
      }
    }

    // Markdown file (top-level)
    if (lowerPath.endsWith('.md') && !path.includes('/')) {
      try {
        data.markdown = await file.text();
      } catch (e) {
        console.warn('Failed to read markdown:', e);
      }
    }

    // Fallback: raw/full.md
    if (path === 'raw/full.md' && !data.markdown) {
      try {
        data.markdown = await file.text();
      } catch (e) {
        console.warn('Failed to read raw/full.md:', e);
      }
    }

    // layout.json
    if (path === 'raw/layout.json' || lowerPath === 'layout.json') {
      try {
        const text = await file.text();
        data.layoutJson = JSON.parse(text);
      } catch (e) {
        console.warn('Failed to parse layout.json:', e);
      }
    }

    // Images
    if (path.startsWith('images/') && /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(path)) {
      const objectUrl = URL.createObjectURL(file);
      data.images.set(path, objectUrl);
    }

    // Raw images (also collect)
    if (path.startsWith('raw/images/') && /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(path)) {
      const objectUrl = URL.createObjectURL(file);
      // Map to the path format used in content_list
      const normalizedImgPath = path.replace('raw/', '');
      if (!data.images.has(normalizedImgPath)) {
        data.images.set(normalizedImgPath, objectUrl);
      }
    }

    // PDF file
    if (lowerPath.endsWith('.pdf')) {
      data.pdfFile = file;
    }

    // Also check raw/ for PDF
    if (path.startsWith('raw/') && lowerPath.endsWith('.pdf') && !data.pdfFile) {
      data.pdfFile = file;
    }
  }

  // If markdown is still empty but we have content_list, generate from raw/full.md
  if (!data.markdown && data.contentList.length > 0) {
    data.markdown = generateMarkdownFromContentList(data.contentList);
  }
}

function generateMarkdownFromContentList(contentList: ContentListItem[]): string {
  const parts: string[] = [];
  for (const item of contentList) {
    switch (item.type) {
      case 'text':
        if (item.text_level === 1) parts.push(`# ${item.text}\n`);
        else if (item.text_level === 2) parts.push(`## ${item.text}\n`);
        else parts.push(`${item.text}\n`);
        break;
      case 'image':
        if (item.img_path) parts.push(`![](${item.img_path})\n`);
        break;
      case 'table':
        if (item.table_body) parts.push(`${item.table_body}\n`);
        break;
      case 'list':
        item.list_items?.forEach((li) => parts.push(`- ${li}`));
        parts.push('');
        break;
      default:
        if (item.text) parts.push(`${item.text}\n`);
        break;
    }
  }
  return parts.join('\n');
}

/** Recursively collect files from FileSystemEntry (drag & drop fallback) */
async function collectEntriesRecursive(
  entries: FileSystemEntry[],
  prefix: string,
  result: Map<string, File>
) {
  for (const entry of entries) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => {
        (entry as FileSystemFileEntry).file(resolve, reject);
      });
      result.set(path, file);
    } else if (entry.isDirectory) {
      const dirReader = (entry as FileSystemDirectoryEntry).createReader();
      const childEntries = await new Promise<FileSystemEntry[]>((resolve, reject) => {
        dirReader.readEntries(resolve, reject);
      });
      await collectEntriesRecursive(childEntries, path, result);
    }
  }
}

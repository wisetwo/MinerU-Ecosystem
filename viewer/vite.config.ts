import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'fs'
import path from 'path'
import { lookup } from 'mrmime'

/**
 * Vite plugin: serve local MinerU output directory under /__data__/ prefix.
 * Also provides /__data__/__manifest__.json listing all files in the directory.
 */
function localDataPlugin(dataDir: string): Plugin {
  return {
    name: 'local-data-serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/__data__/')) return next()

        const relativePath = decodeURIComponent(req.url.slice('/__data__/'.length))

        // Manifest: list all files recursively
        if (relativePath === '__manifest__.json') {
          const files: string[] = []
          function walk(dir: string, prefix: string) {
            for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
              const rel = prefix ? `${prefix}/${entry.name}` : entry.name
              if (entry.isDirectory()) {
                walk(path.join(dir, entry.name), rel)
              } else {
                files.push(rel)
              }
            }
          }
          walk(dataDir, '')
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(files))
          return
        }

        // Serve specific file
        const filePath = path.join(dataDir, relativePath)
        // Security: prevent path traversal
        if (!filePath.startsWith(dataDir)) {
          res.statusCode = 403
          res.end('Forbidden')
          return
        }

        if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
          res.statusCode = 404
          res.end('Not found')
          return
        }

        const mime = lookup(filePath) || 'application/octet-stream'
        res.setHeader('Content-Type', mime)
        fs.createReadStream(filePath).pipe(res)
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const demoDataDir = env.VITE_DEMO_DATA_DIR || ''

  if (demoDataDir) {
    console.log(`\n  📂 Demo data dir: ${path.resolve(demoDataDir)}\n  📡 Serving at: /__data__/\n`)
  }

  return {
    plugins: [
      react(),
      tailwindcss(),
      ...(demoDataDir ? [localDataPlugin(path.resolve(demoDataDir))] : []),
    ],
    define: {
      __DEMO_DATA_ENABLED__: JSON.stringify(!!demoDataDir),
    },
  }
})

import { defineConfig, type ProxyOptions } from 'vite'

import vue from '@vitejs/plugin-vue'

import cesium from 'vite-plugin-cesium'

function createApiProxy(): ProxyOptions {
  let lastBackendWarnAt = 0

  return {
    target: 'http://127.0.0.1:8001',
    changeOrigin: true,
    ws: true,
    configure: (proxy) => {
      proxy.on('error', (err, _req, res) => {
        const now = Date.now()
        if (now - lastBackendWarnAt > 30_000) {
          console.warn(
            '[vite] 后端未启动 (127.0.0.1:8001)。请另开终端运行: cd backend && uvicorn main:app --reload --host 127.0.0.1 --port 8001',
          )
          lastBackendWarnAt = now
        }

        if (res && 'writeHead' in res && !res.headersSent) {
          res.writeHead(503, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ detail: 'Backend unavailable' }))
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [vue(), cesium()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': createApiProxy(),
      '/3dtiles': createApiProxy(),
    },
  },
})

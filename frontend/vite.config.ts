import { defineConfig, loadEnv, type ProxyOptions } from 'vite'

import vue from '@vitejs/plugin-vue'

import cesium from 'vite-plugin-cesium'

function createApiProxy(target: string): ProxyOptions {
  let lastBackendWarnAt = 0

  return {
    target,
    changeOrigin: true,
    ws: true,
    configure: (proxy) => {
      proxy.on('error', (err, _req, res) => {
        const now = Date.now()
        if (now - lastBackendWarnAt > 30_000) {
          console.warn(
            `[vite] 后端未启动 (${target})。请确认对应后端服务已运行。`,
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

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_BACKEND_PROXY_TARGET?.trim() || 'http://127.0.0.1:8001'

  return {
    plugins: [vue(), cesium()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': createApiProxy(backendTarget),
        '/3dtiles': createApiProxy(backendTarget),
      },
    },
  }
})

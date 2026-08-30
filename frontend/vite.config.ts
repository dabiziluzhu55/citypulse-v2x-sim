import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'

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

function createDevSourceNoStorePlugin(): Plugin {
  return {
    name: 'citypulse-dev-source-no-store',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.startsWith('/src/')) {
          const setHeader = res.setHeader.bind(res)
          res.setHeader = ((name: string, value: number | string | readonly string[]) => (
            setHeader(
              name,
              name.toLowerCase() === 'cache-control' ? 'no-store, max-age=0' : value,
            )
          )) as typeof res.setHeader
          res.setHeader('Cache-Control', 'no-store, max-age=0')
          res.setHeader('Pragma', 'no-cache')
          res.setHeader('Expires', '0')
        }
        next()
      })
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_BACKEND_PROXY_TARGET?.trim() || 'http://127.0.0.1:8000'
  const usePolling = env.VITE_DEV_USE_POLLING === '1'

  return {
    plugins: [
      createDevSourceNoStorePlugin(),
      vue(),
      cesium(),
    ],
    optimizeDeps: {
      include: [
        '@baidumap/mapv-three',
        'three',
      ],
    },
    cacheDir: env.VITE_CACHE_DIR?.trim() || 'node_modules/.vite',
    server: {
      host: '127.0.0.1',
      port: 5173,
      warmup: {
        clientFiles: [
          './src/components/visualization/AppThreeMapLoader.vue',
          './src/components/visualization/BaiduThreeMap.vue',
        ],
      },
      watch: {
        ignored: [
          '**/node_modules/**',
          '**/.git/**',
          '**/public/3dtiles/**',
          '**/dist/**',
        ],
        ...(usePolling ? { usePolling: true, interval: 1000 } : {}),
      },
      proxy: {
        '/api': createApiProxy(backendTarget),
      },
    },
    build: {
      target: 'es2020',
    },
  }
})

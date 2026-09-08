import fs from 'node:fs'
import path from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin, ViteDevServer } from 'vite'

const URL_PREFIX = '/roadside-media'

function resolveSafeFile(mediaRoot: string, requestUrl: string): string | null {
  const pathname = decodeURIComponent(requestUrl.split('?')[0] ?? '')
  if (pathname !== URL_PREFIX && !pathname.startsWith(`${URL_PREFIX}/`)) return null

  const relative = pathname.slice(URL_PREFIX.length).replace(/^\/+/, '')
  if (!relative || relative.includes('\0')) return null

  const root = path.resolve(mediaRoot)
  const filePath = path.resolve(root, relative)
  if (filePath !== root && !filePath.startsWith(`${root}${path.sep}`)) return null
  return filePath
}

function contentTypeFor(filePath: string): string {
  if (filePath.toLowerCase().endsWith('.mp4')) return 'video/mp4'
  return 'application/octet-stream'
}

function parseRange(header: string | undefined, size: number): { start: number, end: number } | 'invalid' | null {
  if (!header) return null
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim())
  if (!match) return 'invalid'
  const hasStart = match[1] !== ''
  const hasEnd = match[2] !== ''
  if (!hasStart && !hasEnd) return 'invalid'
  let start = hasStart ? Number(match[1]) : size - Number(match[2])
  let end = hasEnd ? Number(match[2]) : size - 1
  if (!hasStart) start = Math.max(0, start)
  if (Number.isNaN(start) || Number.isNaN(end) || start < 0 || end < start || start >= size) {
    return 'invalid'
  }
  return { start, end: Math.min(end, size - 1) }
}

function sendRoadsideMedia(
  mediaRoot: string,
  req: IncomingMessage,
  res: ServerResponse,
  next: (error?: unknown) => void,
): void {
  const url = req.url ?? ''
  const filePath = resolveSafeFile(mediaRoot, url)
  if (filePath == null) {
    next()
    return
  }
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.statusCode = 405
    res.setHeader('Allow', 'GET, HEAD')
    res.end()
    return
  }

  let stat: fs.Stats
  try {
    stat = fs.statSync(filePath)
  } catch {
    res.statusCode = 404
    res.setHeader('Content-Type', 'text/plain; charset=utf-8')
    res.end('roadside media not found')
    return
  }
  if (!stat.isFile()) {
    res.statusCode = 404
    res.end()
    return
  }

  res.setHeader('Content-Type', contentTypeFor(filePath))
  res.setHeader('Accept-Ranges', 'bytes')
  res.setHeader('Cache-Control', 'public, max-age=3600')

  const range = parseRange(typeof req.headers.range === 'string' ? req.headers.range : undefined, stat.size)
  if (range === 'invalid') {
    res.statusCode = 416
    res.setHeader('Content-Range', `bytes */${stat.size}`)
    res.end()
    return
  }

  const start = range?.start ?? 0
  const end = range?.end ?? stat.size - 1
  const length = end - start + 1
  if (range) {
    res.statusCode = 206
    res.setHeader('Content-Range', `bytes ${start}-${end}/${stat.size}`)
  } else {
    res.statusCode = 200
  }
  res.setHeader('Content-Length', String(length))
  if (req.method === 'HEAD') {
    res.end()
    return
  }
  fs.createReadStream(filePath, { start, end }).pipe(res)
}

export function createRoadsideMediaPlugin(mediaRoot: string): Plugin {
  const resolvedRoot = path.resolve(mediaRoot)
  const attach = (server: ViteDevServer | { middlewares: ViteDevServer['middlewares'] }) => {
    server.middlewares.use((req, res, next) => {
      sendRoadsideMedia(resolvedRoot, req, res, next)
    })
  }

  return {
    name: 'citypulse-roadside-media',
    configureServer(server) {
      attach(server)
    },
    configurePreviewServer(server) {
      attach(server)
    },
  }
}

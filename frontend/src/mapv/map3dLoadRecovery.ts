export type Map3dFailureCode =
  | 'module-cache'
  | 'module-load'
  | 'timeout'
  | 'webgl'
  | 'baidu-init'
  | 'scene-assets'
  | 'unknown'

export interface Map3dFailure {
  code: Map3dFailureCode
  message: string
  detail: string
}

function errorDetail(cause: unknown): string {
  if (cause instanceof Error) return cause.message
  if (typeof cause === 'string') return cause
  return '未知三维地图错误'
}

export function classifyMap3dFailure(cause: unknown): Map3dFailure {
  const detail = errorDetail(cause)

  if (/ERR_CACHE_READ_FAILURE/i.test(detail)) {
    return {
      code: 'module-cache',
      message: '浏览器未能读取3D模块缓存，请重试或清理当前站点缓存',
      detail,
    }
  }

  if (/dynamically imported module|module script|loading chunk|chunkloaderror/i.test(detail)) {
    return {
      code: 'module-load',
      message: '3D模块加载失败，可能是浏览器缓存或本地网络异常',
      detail,
    }
  }

  if (/timeout|timed out|超时/i.test(detail)) {
    return {
      code: 'timeout',
      message: '3D模块加载超时，请检查本地服务和网络后重试',
      detail,
    }
  }

  if (/webgl|graphics context|图形上下文/i.test(detail)) {
    return {
      code: 'webgl',
      message: '当前浏览器的 WebGL 环境不可用，可重试或返回 2D 地图',
      detail,
    }
  }

  if (/baidu|百度|\bAK\b/i.test(detail)) {
    return {
      code: 'baidu-init',
      message: '百度三维地图初始化失败，请检查浏览器端AK和地图服务',
      detail,
    }
  }

  if (/3d tiles|tileset|\.glb|manifest|模型|设施|资产/i.test(detail)) {
    return {
      code: 'scene-assets',
      message: '三维场景资产加载失败，可重试或返回 2D 地图',
      detail,
    }
  }

  return {
    code: 'unknown',
    message: '三维地图暂时无法显示，可重试或返回 2D 地图',
    detail,
  }
}

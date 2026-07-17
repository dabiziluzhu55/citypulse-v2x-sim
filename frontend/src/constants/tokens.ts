/**
 * 地图静态资源路径（3D Tiles由前端public/提供）
 * 运行时Token由后端/api/v1/config/map下发，天地图瓦片走后端代理
 */

export { fetchMapConfig, resetMapConfigCache } from '../api/mapConfig'

export const XIONGAN_3DTILES_URL: string =
  import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim() || '/3dtiles/xiongan/tileset.json'

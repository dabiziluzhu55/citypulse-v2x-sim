/**
 * 地图服务 Token 统一配置
 *
 * Token 仅从本机 .env 读取，仓库不包含默认凭据。

 *
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * Cesium Ion Token
 *   用途：加载 Cesium World Terrain（地形）和 OSM Buildings 回退建筑
 *   获取：https://ion.cesium.com/tokens — 免费注册后创建 Token
 *
 * 天地图 Token
 *   用途：3D 模式下的卫星影像底图 + 注记图层
 *   获取：https://console.tianditu.gov.cn/ — 注册开发者账号，创建应用获取 Key
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 */






export const CESIUM_ION_TOKEN: string =
  import.meta.env.VITE_CESIUM_ION_TOKEN?.trim() || ''

export const TIANDITU_TOKEN: string =
  import.meta.env.VITE_TIANDITU_TOKEN?.trim() || ''

export const XIONGAN_3DTILES_URL: string =
  import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim() || '/3dtiles/xiongan/tileset.json'

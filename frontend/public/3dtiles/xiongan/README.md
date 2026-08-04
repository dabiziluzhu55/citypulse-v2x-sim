# 雄安彩色建筑 3D Tiles

此目录存放雄安新区彩色建筑的 3D Tiles 数据，前端 Cesium 3D 地图直接从此目录加载。

## 目录结构

```
xiongan/
  tileset.json    ← 3D Tiles 入口文件
  tiles/          ← 瓦片数据（.b3dm / .glb）
```

## 加载方式

前端通过 Vite `public/` 静态资源机制直接提供服务：
- 开发环境：`http://localhost:5173/3dtiles/xiongan/tileset.json`
- 无需启动后端即可加载 3D 建筑

## 数据来源

雄安新区建筑彩色 3D Tiles，覆盖范围：
- 经度：115.797 ~ 116.113
- 纬度：38.712 ~ 39.154
- 密集建筑区中心：lon ≈ 115.981, lat ≈ 38.985

## 注意事项

- 数据约 200MB，已纳入 Git 版本管理
- 如需更新数据，直接替换本目录内容即可
- `tileset.json` 必须存在于本目录根层级

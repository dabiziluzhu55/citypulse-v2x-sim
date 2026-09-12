# 部署边界修复验证记录

基线：`develop` / `6f18a23e2e74fc32fb94c5a4c1359ec74b484c52`。
修复工作分支：`codex/deploy-boundaries`。
本地代码：`D:/GitCode/citypulse-deploy-fix`。
服务器验证目录：`/home/kemove/devdata1/citypulse-deploy/validation/code`。

## 已定位并修复

- 场景导出顶层导入仿真 session，评估包初始化导入本机 runner，AI runtime 导入仿真模块。
  纯 XML 编译、产物路径、车型数据迁入共享协议包；旧路径保留兼容别名。
- Redis 地图接口仍强制要求 SUMO_HOME；改为只在 local 模式检查，并处理 Redis 不可用状态。
- 缺少 20 个 Backend 预生成道路 GeoJSON；增加批量离线生成步骤，在服务器验证副本生成并读取通过。
- 浏览器地图 Key 构建时固化；改为启动生成 runtime-config.js、显式公开字段白名单、无缓存响应。
- 源码和模板中的高德 Key 字面量已移除；增加基础浏览器 Key 扫描，生产构建忽略开发 VITE Key。
- 必需绿地/水系请求失败被吞掉；改为使场景准备失败且可重试，Nginx 缺失静态数据返回 404。
- 增加服务端运行时 env/secrets 模板、Docker context 排除项和 Backend 独立镜像定义。
- 密集区域场景测试的旧 fixture 缺少编译器已要求的 TLS manifest；补齐测试输入，保留生产校验。

## 通过的验证

1. Python 相关测试 **50 passed**：部署边界、真实启动、配置隔离、场景导出、指标、地图服务/配置、道路转换、密集区域。
2. 独立 Redis 实例下执行真实 Backend lifespan：health=ok；20 个地图 HTTP 接口返回 200。
3. 全新解释器阻止 simulation/libsumo/sumolib/traci 导入，仍完成 Redis 故障降级、地图读取和约 9.3 MB 场景 ZIP 导出。
4. Vue 类型检查和 Vite 生产构建通过；用专门测试字符串注入 VITE 地图 Key，产物中未出现该字符串。
5. 前端绿地/水系相关测试 **8 passed**；public 与 dist 的 20 组清单/40 个 GeoJSON 校验通过。
6. 服务器独立 Nginx 验证：60 个清单/数据请求通过，缺失资源返回 404；未修改系统运行中的 Nginx。
7. 同一构建产物两次生成不同 runtime Key，HTTP 返回新值且 Cache-Control=no-store；服务端测试密钥未出现在响应中。
8. 浏览器 Key 字面量/模板扫描与 git diff --check 通过。

Python 测试使用服务器 `v2x-ai-py310` 加验证专用依赖目录；未向共享 Conda 环境安装或升级包。
验证 Redis 容器和独立 Nginx 在检查后停止并清理，现有开发服务未切换。

## 尚未覆盖 / 外部条件

- Docker frontend 构建在 Docker Hub 的 node/nginx 基础镜像获取阶段长时间等待，随后镜像层下载仍为 0 字节，本次已取消。
  因此不能声称生产镜像已构建验收；本次交付的是构建通过的前端产物与原生 Nginx HTTP 验证。
  Backend Dockerfile 尚未完成实际镜像构建。后续应继续镜像拉取/构建、Compose 联调与业务仿真验收。
- 天地图 Key 当前缺失；百度 Key 已存在于原开发环境，本次未复制或公开真实 Key。
  最终访问域名、地图平台的浏览器应用授权/来源白名单仍需在上线地址确定后验证。
- 10 个路口至少有一类空绿地/水系源数据，属于合法空集合；本次静态数据共 37 个绿地、35 个水系 feature。
  本次没有虚构多边形，也未进行带真实地图 Key 的完整 3D 页面视觉验收。
- 扩展的旧 intersection-environment audit 在 demo_5/demo_6/demo_9 报告道路与建筑遮挡范围不一致。
  此测试和相关场景数据在本修复中未改动，属于已存在的道路/建筑数据问题，未计入上述 8 个通过测试。

正式部署操作与配置说明见 `production_boundaries.md`。

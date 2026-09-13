# fix4：同步原开发版与内置路侧视频

日期：2026-09-13。上游来源：原开发目录 `/home/kemove/devdata1/zrl/citypulse-v2x-sim`，HEAD 与远端 main 同为 `4e5bab4`。原目录仅只读，未同步未跟踪的训练日志和中间检查点。基于部署版 `84d6f4b` 合并，不覆盖 main。

## 本轮内容

- CoV2X 默认模型更新为 `cov2x_offpeak_guard_v2_final`（第 54 代），保留旧模型回滚入口。平峰道路控制使用 Strong MP 回退，车辆建议使用上下文筛选。新模型不是 fix3 的同一算法检查点，不能直接沿用旧效果结论。
- 合入第四章实验配置、批跑与结果后处理脚本；没有自动执行长批量实验。
- 合入 AI 观测/计划控制范围对齐和越界路口裁剪。
- 合入未知会话 WebSocket 4004 与前端停止重连逻辑。将上游新加的 SUMO 异常导入改为 `simulation_protocol.exceptions`，保持 Backend 隔离。
- 合入全路网绿地/水系、生成脚本和缓存/配色调整。
- 按上游精简 PDF，只保留通行效率表；其他指标表和范围脚注不再输出。
- 保留 fix3 快照顺序、终态、可靠采样、3D 回滚和交通流匹配优化，保留运行时地图配置。

## 视频交付方式

原 roadside_media 约 6.6 GB，包含原始采集帧；本次需要展示的成品是 encoded 下 demo_14、demo_15、demo_19 三个 MP4，合计 5,520,710 字节。仅复制这些成品，原目录不改动。

构建前将三文件复制到发布目录 `frontend/roadside-media/`，由 Dockerfile COPY 到 `/usr/share/nginx/html/roadside-media/`。Compose 移除 Frontend 视频挂载，防止旧挂载遮住镜像内容。旧 shared 视频保留供回滚。

视频更新需要重建前端镜像并重新创建前端容器。运行镜像本身包含视频，不再依赖宿主机媒体目录；Git 不提交视频二进制，源码重建者仍需拿到三份 MP4。视频不是实时监控。

全量构建：`docker build -f frontend/Dockerfile -t citypulse-frontend:<新标签> frontend`。增量构建：先在 frontend 执行 `npm run build`，再于根目录执行 `docker build -f deploy/Dockerfile.fix4-frontend -t citypulse-frontend:develop-4e5bab4-fix4 .`。两种方式均要求上述媒体暂存目录存在。

## 发布操作

新发布目录：`/home/kemove/devdata1/citypulse-deploy/releases/develop-4e5bab4-fix4`。

三个应用镜像标签：`develop-4e5bab4-fix4`。依赖沿用 fix3 镜像，无需修改原 Conda。配置与会话数据沿用 acceptance，地图仍挂载旧 acceptance 路网目录，不能删除该目录。

```bash
cd /home/kemove/devdata1/citypulse-deploy/releases/develop-4e5bab4-fix4
export CITYPULSE_ACCEPTANCE_ENV=/home/kemove/devdata1/citypulse-deploy/config/acceptance/acceptance.env
cpcompose() { docker compose --env-file "$CITYPULSE_ACCEPTANCE_ENV" -f deploy/compose.acceptance.yml "$@"; }
cpcompose ps
curl -fsS http://127.0.0.1:18080/api/v1/health
```

SSH 隧道及地图 Key 更新步骤与运行说明相同。`CITYPULSE_MEDIA_DIR` 旧配置即使保留，也不再被 fix4 Compose 使用。

回滚时在无活动会话的维护窗口恢复旧标签，并使用 fix3 目录下的 Compose；旧版 Compose 仍需 shared 视频挂载。不要删除 shared 数据、Redis 卷或旧镜像。

## 检查与保留项

合并代码前端 72 项测试、服务器后端/AI/报告/新模型等 59 项测试通过（一次依赖弃用警告）。后续构建、运行与视频检查记录见服务器 `validation/fix4/`。

本次发布验证：

- 前端 Vue 类型检查、Vite 构建和静态资源检查通过。服务器既有依赖目录的 `.bin/vue-tsc` 入口失效，构建实际直接调用 `node node_modules/vue-tsc/bin/vue-tsc.js --noEmit` 和 `node node_modules/vite/bin/vite.js build`，未修改原环境依赖。
- Backend 镜像执行禁止 SUMO 导入的真实生命周期检查通过；运行接口为 `ok/redis`。
- Frontend 容器 Mounts 为空，确认媒体来自镜像。三个 MP4 与原编码文件 SHA-256 一致。
- 浏览器三视频均为 1280×720，时长分别约 64.3、64.0、63.6 秒，可播放并跳转到第 3 秒，Range 请求返回 206。
- 全路网 green/water 资源返回 200，分别包含 135/47 个 Feature；首次页面检查没有 JavaScript 异常。3D 建筑仍需加载，不能将资源可达等同于所有视觉问题解决。
- 新 CoV2X：60 秒仿真墙钟约 17.61 秒完成，最终指标 finished=true。
- AI：90 秒仿真墙钟约 99.55 秒完成，INACTIVE → ACTIVE → RECOVERY → FINISHED，计划序号 5，最终指标 finished=true，没有 AI 错误。
- Worker 默认模型及 SHA-256 与上游最终检查点一致：`25a5df603b851a34ca3507f0547b0c9c6013683d31b54b53500146cfea898946`。

最终镜像 ID：

| 服务 | SHA-256 |
|---|---|
| Backend | `4ade981385c528d55ca891b38f44dc154773ada7b48d02389dccd82d25c546c39` |
| Worker | `2d2db28649f9f92ae53329337a978a41cb51d850db950cbdcc78fcf43ec3bac9` |
| Frontend | `dacfa361d4161292a901723a372f02cf8811f13a9283a39eb229088090bb0d37b` |

切换前已确认无活动会话，旧配置备份为 `config/acceptance/acceptance.before-fix4.env`。本轮测试只创建并清理自己的两份测试会话。

CoV2X 过程提升正值保留和缺基线提示不足未在本轮修改；百度授权问题、3D 建筑加载延迟仍是保留项。新模型短测仅验证可运行，不代表完整算法优越性或长期稳定性证明。

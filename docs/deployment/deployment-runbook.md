# CityPulse fix3 部署与运行说明

更新：2026-09-12。适用范围：现有服务器的 SSH 隧道验收部署，以及在保留相同配置、数据和镜像条件下恢复该部署。不是公网发布方案。

## 1. 当前状态与边界

本次只读复查：四个容器均运行，Backend、Redis 健康；HTTP 健康接口为 `ok/redis`，地图产物和会话目录就绪，Worker Celery ping 返回 `pong`。数据盘约 11 T，总使用率 70%，可用约 3.2 T。这些是检查时状态，不代表持续监控。未重启服务、未停止用户会话。

本地复跑评估对照、提升卡片和报告测试共 48 项，全部通过。仍有界面和外部地图保留项，见第 12 节；不能据此宣称没有任何问题。

| 项目 | 位置/值 |
|---|---|
| SSH | `kemove@frp-fun.com`，端口 `49760` |
| 原服务器开发版 | `/home/kemove/devdata1/zrl/citypulse-v2x-sim`，不修改、不删除、不启停其服务 |
| 原 Conda | `v2x-ai-py310`，部署容器不向其安装或升级包 |
| 本地部署源码 | `D:/GitCode/citypulse-deploy-fix`，`codex/deploy-boundaries` |
| 基线 | develop 的 `6f18a23e2e74fc32fb94c5a4c1359ec74b484c52` |
| 当前发布目录 | `/home/kemove/devdata1/citypulse-deploy/releases/develop-6f18a23-fix3` |
| Compose 项目 | `citypulse-acceptance` |
| 应用镜像标签 | `develop-6f18a23-fix3`，不是 Git 提交号 |
| 私有配置 | `/home/kemove/devdata1/citypulse-deploy/config/acceptance` |
| 持久数据 | `/home/kemove/devdata1/citypulse-deploy/shared/acceptance` |
| Web 入口 | 服务器 `127.0.0.1:18080`，通过 SSH 隧道访问 |

本部署源码与说明随本次部署交付提交进入 develop；历史记录中的“未提交”描述对应当时状态。获取时应确认 develop 包含本次部署交付提交。源码之外仍需实际镜像、私有配置和数据，main 不参与这些操作。

## 2. 服务和目录关系

浏览器 → SSH 隧道 → Frontend/Nginx → Backend → Redis → SUMO Worker。Backend 读取共享会话产物并提供状态、指标、预测和 AI 接口；Worker 执行 SUMO。Backend 不安装 SUMO 内核。

预测模型与嵌入模型使用 CPU。大模型调用现有宿主机 Qwen 服务 `http://host.docker.internal:8001/v1`，模型名 `traffic-qwen-v2`。该服务不由本 Compose 启停；其 GPU 消耗也不包含在四个部署容器的 `docker stats` 中。

目录职责：

- `releases/`：发布源码及构建材料；保留旧版本便于回滚。
- `config/acceptance/acceptance.env`：Compose 路径、标签和网络参数。
- `config/acceptance/backend.env`、`worker.env`：容器运行参数。
- `config/acceptance/browser.env`：浏览器地图 Key；值会发送到浏览器。
- `config/acceptance/secrets/`：服务端认证文件，不放入浏览器配置。
- `shared/acceptance/sessions/`：会话数据、评价快照等。
- `shared/acceptance/rag/`：RAG 索引；`models/`：模型；`roadside-media/`：视频。
- Redis 数据由 Compose 命名卷持久化，不在 sessions 目录内。

**现有地图挂载仍指向旧 acceptance 发布目录：**

```text
/home/kemove/devdata1/citypulse-deploy/releases/develop-6f18a23-acceptance/data/maps/sumo
```

因此旧 acceptance 目录目前是有效运行依赖，不能因为已发布 fix3 就删除。以后若要迁入 shared，应先复制完整数据、校验内容，再在维护窗口更改 `CITYPULSE_MAPS_DIR` 并重新创建 Backend/Worker；本次未执行迁移。

## 3. 连接和日常启动

在个人电脑 PowerShell 执行，按终端提示输入 SSH 密码：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 18080:127.0.0.1:18080 -p 49760 kemove@frp-fun.com
```

保持窗口运行，浏览器打开 `http://127.0.0.1:18080/`。同伴在各自电脑建立隧道。若本地端口被占用，先确认是否已有可用隧道；也可将 `-L` 左端改成 18081，并访问对应地址。不同端口会形成不同浏览器存储空间，原有评估对照记录不会自动共享。

另开终端登录服务器：

```powershell
ssh -p 49760 kemove@frp-fun.com
```

以下均为服务器 Bash 命令。每次新登录先定义本次部署命令：

```bash
cd /home/kemove/devdata1/citypulse-deploy/releases/develop-6f18a23-fix3
export CITYPULSE_ACCEPTANCE_ENV=/home/kemove/devdata1/citypulse-deploy/config/acceptance/acceptance.env
cpcompose() { docker compose --env-file "$CITYPULSE_ACCEPTANCE_ENV" -f deploy/compose.acceptance.yml "$@"; }
cpcompose ps
```

服务正常时不用重复重启。需要启动已停止的部署时执行 `cpcompose up -d`。`restart: unless-stopped` 会处理通常的容器退出；被人工停止的服务仍需人工启动。

## 4. 健康检查与日志

```bash
cpcompose ps
curl -fsS http://127.0.0.1:18080/api/v1/health
cpcompose exec -T worker python -m celery -A simulation.sumo.engine.distributed.celery_app:app inspect ping --timeout 5
cpcompose logs --tail 100 backend worker frontend
docker stats --no-stream
df -h /home/kemove/devdata1/citypulse-deploy
du -sh /home/kemove/devdata1/citypulse-deploy/shared/acceptance/sessions
```

健康接口应显示 `status=ok`、`simulation_manager_mode=redis`、`generated_artifacts_ready=true`、`redis_ready=true`。`sumo_home_configured=false` 在隔离后的 Backend 中是正常状态。Worker 没有 Compose healthcheck，容器为 Up 还需结合 ping、任务状态和日志判断。

健康接口的 `recommended_uvicorn_workers` 不是实际运行进程数，也不是要求直接照填的配置。当前镜像启动一个 Uvicorn worker；调整并发前需验证会话恢复、内存和任务负载。

## 5. 仿真、评价和报告的完整操作

### 5.1 普通仿真

1. 页面就绪后选择场景、交通时段、展示时间、扰动和管控算法。
2. 开始仿真，确认进入 RUNNING，时间、车辆数和速度曲线持续变化。
3. 在 2D/3D 中核对车辆。刚开始或当前路口暂时无车，不等于全网无车；先看车辆总数和其他路口。
4. 暂停用于临时查看；结束会提前终止本轮。用于完整算法评价时优先自然运行至配置终点，并等待最终评价完成。
5. 导出当前场景可保存仿真配置/工程；右侧评价报告是另一份产物，需等评价可导出。

播放倍率是目标推进倍率，实际倍率取决于计算和渲染负载。截图中出现计算繁忙提示时应看实际推进值，不应仅依据设置倍率判断仿真时间。

### 5.2 为什么提升卡片显示横线

上方四项是相对于固定配时的百分比，不是四项绝对值。当前代码要求同一比较组已有 `fixed` 且 `finished=true` 的评价点。未完成基线时，即使车辆数和曲线正常，四项仍为 `—`。

正确对照顺序：

1. 固定场景、受控路口范围、交通时段、起始时间、时长、车流来源、扰动、seed 和仿真步长。
2. 选择固定配时，完整运行并取得最终评价。
3. 保持上述配置不变，切换 CoV2X、Max Pressure 等算法，再运行。
4. 比较同一时间进度的过程值，或双方最终值。不要拿不同扰动或不同仿真时长的结果直接计算提升。

切换算法、播放速度或观看路口不改变比较组；修改会影响结果的配置则会形成不同组。基线指标为零、缺失或无有效数值时，对应提升也为 `—`，不是 0%。

比较记录使用浏览器 localStorage，最多保留 8 个组，每轮最多 200 个采样点。不同浏览器、主机名或端口不共享；清除站点数据会丢失本地比较索引，即使服务器会话文件仍在。不要将浏览器存储当作长期科研数据备份。

### 5.3 CoV2X 过程卡片的已知限制

原版已有逻辑会保留最近一次有意义的正向提升值，运行中指标变差时，显示值可能仍为先前正值。它不是实时效果的可靠证明；终态使用真实评价值。正式结论应以同条件最终评价与报告为准。本次复查确认了这一问题，没有修改其显示逻辑。

建议后续改为显示原始有符号提升，并标注“过程值/最终值”；无基线时明确给出操作提示。现有测试通过只说明符合现有实现，不代表该显示策略合理。

### 5.4 报告

本组至少存在一份完成的评价后，导出按钮才可用。可导出不等于六种算法都已运行，更不代表已有固定配时对照。报告只应将实际完成的算法作为证据；未运行的算法不能解释为零值。

## 6. AI 管控验收

先确认 Qwen 服务可访问、模型配置与认证可用，再进行短时闭环验证。API 总体健康不保证大模型推理正常。

建议沿用已验证的小场景：Max Pressure 基础算法，路口 14 限速扰动，开启 AI 管控；设置扰动在仿真窗口内开始和结束。观察 AI 状态、计划序号和错误信息，预期经过 INACTIVE → ACTIVE → RECOVERY → FINISHED，并最终完成仿真。

已有 fix3 记录使用 90 秒仿真、扰动 15–50 秒，墙钟约 100 秒完成，计划序号达到 5。后续短时复测控制在约两分钟，不自动开展 20 分钟持续测试。自然语言助手的 RAG 检索需另外验收，不能用 AI 状态转换代替检索验证。

## 7. 环境与地图 Key

现有配置以私有文件为准，示例文件不能直接覆盖实际配置。特别是 `deploy/backend.env.example` 中 Qwen 主机名为示例，本服务器实际使用 `host.docker.internal`。

| 参数 | 当前用途 |
|---|---|
| `CITYPULSE_RELEASE_TAG` | 三个应用镜像标签 |
| `CITYPULSE_MAPS_DIR` | 完整 SUMO 产物根目录，容器只读挂载 |
| `CITYPULSE_SHARED_DIR` | sessions 和 RAG 持久数据根目录 |
| `CITYPULSE_EMBEDDING_DIR` | `shared/acceptance/models/Qwen3-Embedding-0.6B` |
| `CITYPULSE_MEDIA_DIR` | `shared/acceptance/roadside-media` |
| `CITYPULSE_ACCEPTANCE_SUBNET` | 当前 `172.21.0.0/16` |
| `SIMULATION_MANAGER_MODE` | Backend 使用 `redis` |
| `CITYPULSE_REDIS_KEY_PREFIX` | Backend/Worker 均为 `citypulse-acceptance` |
| `CITYPULSE_LLM_BASE_URL` | `http://host.docker.internal:8001/v1` |
| `CITYPULSE_LLM_MODEL` | `traffic-qwen-v2` |
| `RAG_EMBEDDING_MODEL_PATH` | 容器内 `/models/embedding` |
| `RAG_INDEX_DIR` | 容器内 `/app/outputs/rag/traffic_knowledge_chroma` |

Backend/Worker 的 broker、state、result Redis URL 应互相匹配，分别使用 Redis 数据库 0、1、2。保持当前线程限制和 Worker 并发 4，未经测试不要提高并发。

浏览器配置字段：`CITYPULSE_BROWSER_BAIDU_AK`、`CITYPULSE_BROWSER_TIANDITU_TOKEN`、`CITYPULSE_BROWSER_CARTO_KEY`，另有可选高德字段。本文不记录真实值。天地图和 Carto 已配置并有成功浏览器请求记录；百度有 AK 但在线瓦片仍可能 403。

更换 Key：编辑私有 `browser.env`，然后在维护安排允许时执行：

```bash
cpcompose up -d --no-deps --force-recreate frontend
curl -fsSI http://127.0.0.1:18080/runtime-config.js
```

刷新页面并在浏览器核对瓦片响应及水印；runtime-config.js 应返回 no-store。无需重新构建镜像，单纯 `restart` 不会重新读取 env_file 中的新值。重建前端会短暂断开页面连接，先保存当前工作。

服务端认证通过独立 env/secret 配置；当前 Compose 没有为 Frontend 挂载 secrets，因此不能只填浏览器 `_FILE` 路径就假定文件存在。正常使用当前 browser.env 即可。

## 8. 发布构建与首次迁移

### 8.1 在现有服务器重建 fix3

fix3 实际采用基于 fix2 的增量镜像，适合保留原依赖。以下是重建命令参考，重新构建前需保证源码是完整修复版，且没有运行中的会话。已运行的部署不需要为阅读本文执行这些命令。

```bash
docker image inspect citypulse-backend:develop-6f18a23-fix2 >/dev/null
docker image inspect citypulse-worker:develop-6f18a23-fix2 >/dev/null
docker image inspect citypulse-frontend:develop-6f18a23-fix2 >/dev/null
docker build -f deploy/Dockerfile.fix3-backend -t citypulse-backend:develop-6f18a23-fix3 .
docker build -f deploy/Dockerfile.fix3-worker -t citypulse-worker:develop-6f18a23-fix3 .
```

前端先用兼容 Node 和已有锁文件构建。现有 fix3 使用 Node 22.23.1；全量 Dockerfile 的 Node 20 为另一构建路径，不代表两种路径已重复做完全一致性认证。

```bash
cd frontend
npm ci
npm run build
cd ..
docker build -f deploy/Dockerfile.fix3-frontend -t citypulse-frontend:develop-6f18a23-fix3 .
```

`npm ci` 会重建本目录依赖，只在部署发布目录执行，不能借用并改写原开发目录的 node_modules。也可使用全量定义：仓库根执行 Backend/Worker 构建，Frontend 使用 `frontend` 为构建上下文；全量重建受外部源和未完全锁定的基础镜像影响，需重新验收。

```bash
docker build -f backend/Dockerfile -t citypulse-backend:develop-6f18a23-fix3 .
docker build -f deploy/Dockerfile.worker -t citypulse-worker:develop-6f18a23-fix3 .
docker build -f frontend/Dockerfile -t citypulse-frontend:develop-6f18a23-fix3 frontend
```

上面是两种替代构建路径，不要无目的连续执行覆盖镜像。当前准确镜像 ID 见 [fix3 验证记录](fix3-correctness-and-traffic-optimization.md)。

### 8.2 迁移到另一台服务器

交接包至少需要：完整修复源码、三个应用镜像与 Redis 镜像、私有配置及 secrets、完整地图生成数据、模型、RAG、路侧视频、需保留的会话及 Redis 卷备份。仅源码或仅 Compose 不足以复现。

优先导出已验证镜像，而不是依赖从外网临时重建：

```bash
docker save -o /path/to/new-backup/citypulse-fix3-images.tar citypulse-backend:develop-6f18a23-fix3 citypulse-worker:develop-6f18a23-fix3 citypulse-frontend:develop-6f18a23-fix3 redis:7.4-alpine
```

`/path/to/new-backup` 为事先创建的备份位置，不能原样执行。目标机导入镜像并复制数据后，逐项修改 acceptance.env 的绝对路径，检查挂载权限、18080 端口和 Docker 子网冲突，再启动 Compose。

Qwen 必须单独准备或指向可访问的服务；当前防火墙只允许部署网段访问宿主机 8001，换网段/网关要同步调整。不要为此改动原开发服务或共享 Conda。没有完成另一台空白服务器的全流程恢复演练，迁移后必须重新验收。

道路 GeoJSON 应在带 SUMO 的独立生成环境中离线生成：`python scripts/deployment/build_map_artifacts.py --generated-dir <部署数据副本的generated目录>`，默认包括 600/900 米。它需要已有完整路网和清单，不能从空目录生成整个项目数据，也不能在只读挂载的 Backend 中运行。

## 9. 更新、停机与回滚

更新前确认没有 RUNNING/PAUSED/QUEUED 的待保留会话，记录当前镜像 ID、配置版本和发布目录，并备份。新版本使用新目录和新标签；不要直接覆盖旧发布作为唯一备份。

```bash
cpcompose config --quiet
cpcompose up -d
cpcompose ps
```

上述启动使用 acceptance.env 指定标签，不会自动构建镜像。更新 Backend/Worker 会影响正在执行的任务，不能以“不修改原版”为由忽略部署版会话。

停止部署：`cpcompose stop`；恢复：`cpcompose up -d`。不要使用 `down -v` 删除 Redis 卷。

回滚 fix2：先结束部署版会话，核对 `config/acceptance/acceptance.before-fix3.env` 与现有配置差异，仅将所需发布标签恢复为 `develop-6f18a23-fix2`，使用旧 acceptance 发布目录的 Compose 创建三个应用容器。保留当前地图 Key、共享数据和 Redis 卷，再做健康检查。不要盲目用旧 env 覆盖后来补充的参数。回滚也会失去 fix3 的正确性修复，只作为故障恢复路径。

## 10. 备份与容量

备份对象包括源码/镜像、私有配置、地图、模型、RAG、视频、sessions 和 Redis 数据卷。认证信息的备份应保持私有权限。为获得一致备份，应在无活动会话时安排暂停部署写入，再备份会话与 Redis 卷，完成后恢复。

fix3 的 evaluation.sqlite 保存每个已发布快照，当前三份 300 秒记录约 21–33 MiB/会话；规模、车辆数和发布频率不同，占用也不同。这不是长期容量上限。

目前没有新增自动保留/清理策略。先导出需要的报告和证据，再由维护者确定归档周期；不能按“目录旧”自动删除仍被挂载的地图目录或运行会话。浏览器 localStorage 的 8 组限制不会清理服务器数据库。

## 11. 常见问题排查

| 现象 | 优先检查 |
|---|---|
| 页面打不开 | SSH 隧道是否存活、本地端口是否冲突、服务器 Frontend 是否 Up |
| API 502 | Backend 是否健康、Nginx 日志和 Docker DNS；fix3 已增加动态解析 |
| 卡在排队 | Worker ping、任务队列与并发占用、Backend/Worker Redis 配置是否匹配 |
| 车辆数有值但没看到车 | 当前路口和视角、车辆经纬度、页面错误；不要只判断总计数 |
| 四个提升值都是横线 | 是否有同配置的完成固定配时基线；单项还要检查零基线/缺失值 |
| CoV2X 过程提升与曲线不一致 | 原版保留最近正值的显示策略；以最终评价核对 |
| 报告按钮灰色 | 当前比较组是否有最终评价；检查失败状态和终态结算 |
| Carto 水印 | 浏览器 Key 是否生效、容器是否重建、瓦片授权/额度 |
| 天地图缺图 | 浏览器请求、域名限制和 Token；服务器直接请求失败不等于浏览器不可用 |
| 百度 403 | 已知外部授权/兼容性问题，有 AK 不保证服务可用 |
| 3D 切换后建筑迟到 | 瓦片网络、解码和浏览器 GPU；失败回滚修复不等于加载加速 |
| AI 超时 | 外部 Qwen 可达性、认证、模型负载及 AI 日志；不要只看 API health |
| 重启后比较记录不见 | 是否换浏览器/端口、清过站点数据；服务器与浏览器记录分开保存 |

## 12. 尚待处理与验收范围

当前未发现新的服务启动阻塞，但仍有：CoV2X 过程正值保留导致的展示偏差、缺基线提示不足、3D 加载延迟、百度在线瓦片问题，以及按约定暂缓的道路/建筑问题。地图仍挂载旧 acceptance 目录、无自动数据清理，也属于交付维护事项。

本轮只读检查没有重新创建用户仿真，没有再次验证全部 AI/地图链路。历史短时 AI、三场景同输入对照与性能证据见 [改动汇总](deployed-vs-original-change-summary.md) 和 [fix3 详细验证](fix3-correctness-and-traffic-optimization.md)。短测通过不证明长期无内存增长或任意并发都稳定。

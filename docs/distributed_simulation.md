# Redis/Celery 多会话仿真

`RedisSimulationManager` 在保留本地 `SimulationManager` 的同时，为后端提供跨进程的
SUMO 会话队列。每个 Celery prefork 子进程只运行一个进程内 libsumo 会话，因此不同会话
不共享 libsumo 全局状态。

## 安装和启动

真实服务器应为 Linux，并已全局配置 SUMO。先安装依赖并启动仅监听本机的 Redis：

```bash
pip install -r requirements.txt
docker compose -f compose.redis.yml up -d
docker compose -f compose.redis.yml ps
```

运行包含 fakeredis repository 检查的完整测试集时安装：

```bash
pip install -r requirements-test.txt
python -m unittest discover -s tests -p 'test_*.py'
```

根据 `.env.example` 导出环境变量。Celery 不会自动读取 `.env`；可以由 systemd、部署
脚本或 shell 显式注入。启动四进程 worker：

```bash
celery -A simulation.sumo.distributed.celery_app:app worker \
  --queues citypulse-sumo \
  --pool prefork \
  --concurrency "${CITYPULSE_SUMO_WORKER_CONCURRENCY:-4}" \
  --loglevel INFO
```

必须保留 `--pool prefork`。不得改用 threads、gevent 或 eventlet；libsumo 是进程内单例，
`--concurrency N` 表示 N 个相互隔离的 libsumo 子进程。应用配置也将默认 pool 固定为
prefork，进程内所有权锁会拒绝任何误配置造成的并发 runtime。

Redis 使用数据库 0 作为 broker、数据库 1 保存会话、数据库 2 保存 Celery 结果。
如使用带密码或远端 Redis，在三个 URL 中配置完整凭据。不要把 Redis 的 6379 端口暴露
到公网。

## Python 接口

```python
from simulation.sumo import RedisSimulationManager, SimulationConfig

manager = RedisSimulationManager()
session_id = manager.start(
    SimulationConfig(
        intersection_ids=("demo_2",),
        period="morning_peak",
        duration_seconds=600,
        gui=False,
        start_paused=True,
        playback_speed=1.0,
    )
)
snapshot = manager.snapshot(session_id)
```

新会话首先处于 `QUEUED`。worker 领取后依次进入 `STARTING` 和 `RUNNING` 或
`PAUSED`。排队时可查询、订阅、等待或停止；暂停、倍速和运行时事件命令需要等到
`STARTING` 之后。分布式模式严格使用 libsumo 并拒绝 `gui=True`，图形调试继续使用本地
CLI 的 TraCI/sumo-gui 旁路。

后端只需把全局管理器的构造替换为 `RedisSimulationManager(...)`，其余
`catalog/start/snapshot/subscribe/wait/stop/pause/resume/event` 方法保持一致。
如果构造函数传入了自定义 `redis_url`、`generated_dir` 或 `session_root`，必须让 Celery
worker 的 `CITYPULSE_REDIS_STATE_URL`、`CITYPULSE_SUMO_GENERATED_DIR` 和
`CITYPULSE_SUMO_SESSION_ROOT` 指向相同的 Redis 与共享目录。

## 运维

查看队列和活动任务：

```bash
celery -A simulation.sumo.distributed.celery_app:app inspect active
celery -A simulation.sumo.distributed.celery_app:app inspect reserved
docker compose -f compose.redis.yml exec redis redis-cli INFO persistence
```

优雅停止 worker 时发送 `TERM`，等待活动任务结束；需要立即结束某个仿真时应先调用
`manager.stop(session_id)`。终态 Redis 数据默认保存 24 小时，
`outputs/sessions/<session_id>/` 中的诊断文件不会自动删除。

若 worker 子进程异常退出，Celery 将任务标记为失败。管理器在读取或订阅会话时结合
15 秒心跳和 Celery result 将残留活动状态收敛为 `FAILED`，不会自动重试同一仿真。

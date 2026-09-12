# 两项验收问题定位

本轮只定位和复现，未修改应用代码、未更新部署、未触碰原开发目录/main。

## 3D 场景切换

`frontend/src/components/visualization/BaiduThreeMap.vue` 的 `switchRealisticIntersection`：

1. 1591–1619 行附近创建 cameraPromise，调用 focusIntersection，镜头已开始移动。
2. 1621–1645 行附近等待车辆和镜头提交条件；车辆未就绪时 discard 后直接 return false，没有恢复镜头、路口选择或场景状态。此分支没有进入 catch。
3. 1649–1657 行附近 commitViewportTransition 失败会抛异常。
4. 1694–1714 行附近 catch 通过 restoreCommittedIntersection 恢复路口选择，没有恢复 mapView 的镜头中心、anchorId、viewport。
5. `useActiveIntersectionScene.ts:56` 的恢复函数只修改选择和状态；`BaiduThreeMap.vue:2466` 又明确跳过回滚选择引发的 watcher，因此不会通过重新切换自动恢复镜头。

上述是可从控制流确认的失败恢复缺口。原对照运行出现 viewportStageStatus=failed，符合 catch 路径，但之前没有保存浏览器 console.warn，因此无法断言当时究竟是车辆首帧、几何一致性还是其它具体异常触发。车辆准备等待上限在 waitForViewportVehicleStage 内为 20 秒，车辆过渡预热另为 8 秒。不能将缺口已定位说成当次首个触发异常已完全查明。

修复方向：将镜头状态纳入切换事务；所有当前事务失败路径一致回滚场景、选择、镜头，过时/取消的事务不得覆盖新选择；保留原始失败原因和阶段。

## 评价数据链路

### 已复现的 Redis 倒序缺口

`simulation_protocol/client.py:340` 的 RedisSnapshotSubscription.get：Pub/Sub 暂无数据时读取最新快照；随后收到旧消息时只比较序列化文本是否相同，没有比较 sequence 或仿真时间。因此可返回 1 → 3 → 2 → 3。

`.codex-backups/diagnostic_repro.py` 从当前文件抽取实际类定义运行，通过可控 Pub/Sub 假对象稳定复现该序列，无需运行服务器仿真。

### 已复现的区域累计重复

`traffic_eval/collector.py:663` 的 _SceneMembershipTracker.update 没有顺序保护，约 699–719 行对增量取 max(0, new-old) 后，无条件更新上次累计读数。旧帧将上次值降低，再次收到新帧就重复累计。

同一复现中，正常 [1,2,3] 得到等待累计 2、制动累计 2；[1,3,2,3] 得到 3、3。`TrafficMetricsCollector._observe:287` 虽过滤时间倒退，但不保护独立 tracker；外层 observe_snapshot 在约 835 行仍调用 tracker.update，不能阻止其内部状态被旧帧污染。

### 原版采样不完整

`simulation/sumo/engine/session.py:342` 为订阅创建 maxsize=1 队列，_publish:399–409 在队列满时删除旧帧。`backend/app/services/simulation_service.py:578` 的指标 watcher 使用此订阅，同时处理指标、预测、历史和 AI。加速仿真下处理来不及时，中间帧可被覆盖。区域进入/离开、等待与制动统计依赖中间样本，最终车辆快照相同不能保证统计相同。

### 对实测差异的解释边界

确认的是以上代码缺口和可复现的错误机制。之前基准未保存每一帧进入指标模块的序列，因此不能量化它们分别造成 west_dense 等场景差异的多少，也不能认定 Redis 乱序在每组基准都实际发生。需修复顺序/去重与可靠采样后，以固定输入回放或补充逐帧记录验证结果。

修复方向：订阅层拒绝旧序号；指标入口在修改任何状态前统一拒绝倒序和重复帧；累计评价使用完整、有序的仿真样本或 worker 端权威累计，不能继续依赖为实时显示设计的最新帧队列。

# 可恢复的远程视频阶段调度（优化 2）

## 执行时序

旧设计：一个 worker 持有单实例锁，领取项目 A 后在一次 `generate_video()` 内执行 POST → 循环 GET/睡眠 → 下载 → 质检。云端等待没有占用 GPU 锁，却长期占用唯一项目执行器；项目 B 不能被消费。

新设计仍只有一个 SQLite 项目队列、一个 LangGraph 检查点体系、一个本地 GPU 锁命名空间：

```text
A remote_submit：授权/版本校验 → 原子预算预留 → fsync 意图 → POST → 保存 task_id
  → job queued(next_poll_at)，释放本次调用与执行器
B local_render / 创作 / quality_review：推进不冲突的工作
A remote_poll（到期）：一次 GET → 保存状态与 next_poll_at → 再次让出
B remote_submit：只有并发云任务总数未达 2 且预算获授权时提交
A remote_poll：Success → 保存 file_id → remote_download 到期入队
A remote_download：获取下载地址 → 有界下载/校验 → quality_review
```

每个生产 `advance` 使用 `production_config.remote_step=True`。`ProductionDeferred` 是正常等待信号，不是人工审批或生产错误；worker 以带 lease 的 CAS 把原 job 放回队列。重新进入原 LangGraph 生产节点会先检查素材缓存、已有 take 和 provider 回执，不会重放生成 POST。低层适配器的同步调用保留兼容，但新 POST 仍要求预算模块的 `before_submit`；生产 worker 不走同步轮询等待。

## 最小数据库迁移

`store.initialize()` 对同一个 `studio.sqlite3` 做可重复的增量迁移，`PRAGMA table_info` 检测后执行 `ALTER TABLE`，整组新增字段在 `BEGIN IMMEDIATE` 中完成。

| 位置 | 字段 |
| --- | --- |
| jobs | next_poll_at REAL DEFAULT 0；phase TEXT DEFAULT local_render；lease INTEGER DEFAULT 0 |
| cloud_tasks | next_poll_at REAL；phase TEXT DEFAULT remote_submit；poll_failures INTEGER DEFAULT 0 |
| jobs_due | status / next_poll_at / created 索引 |
| factory_migrations | remote_phases_v1 迁移记录 |

不重写旧项目 JSON，不清理历史 take、task_id、file_id、预算或授权。旧 queued job 默认立即可运行。上线前停止对应 worker 并做包含 WAL 的一致性备份；新 worker 取得原 `worker.lock` 后才调用 `recover_jobs()`。回滚只回滚代码，保留新增列/表；不要用旧代码继续付费生产，因为旧版没有全部授权保护。

## 并发、故障与资源边界

- 进程级 worker.lock 保留，本地 GPU 的 Scheduler/GpuLease 不变；没有 Celery、Redis、新状态服务或另一套 Agent 编排。
- 全局最多 **2 个未终结的云意图**。包括提交结果未知的意图；未知结果不会因超时、重启、换 take 自动释放并发位或费用预留。计数检查和新意图插入同属预算事务。
- 领取以 SQLite `BEGIN IMMEDIATE` 串行化；同项目存在 running job 时不能领取其第二条唤醒记录。每次领取/恢复增加 lease，过期执行结果不能结束或重排新 lease。
- 每次远程阶段最多一次生成 POST 或一次状态 GET。下载阶段包含文件地址查询与一次有截止时间/大小限制的下载；它不是无界云端睡眠。
- GET/下载暂时失败按原轮询间隔指数退避，上限 300 秒；连续第 6 次失败转人工处理。ID 不匹配、未知状态等协议错误不自动重试。未知 POST 直接阻断，绝不变成定时重提。
- 云端等待计入累计 `elapsed_seconds`，另记 `remote_wait_seconds`；领取或人工暂停时结算。人工暂停时间不计入；恢复不会清空费用、GPU 时间或等待记录。
- 暂停不撤销已经发出的供应商任务，只阻止新的付费意图并保留查询/下载凭据。供应商侧取消/退款需要独立人工核对。

## 使用与测试

正常使用原 `python -m factory.worker`，无需启动其他调度服务。工作台显示“云端等待 · 执行器已让出”并继续轮询项目状态。GET 云费用账本能看到每个任务的 phase、next_poll_at、poll_failures。

```bash
python -m pytest -q tests/test_factory_remote_tasks.py
python -m pytest -q tests/test_factory_*.py
node --test tests/test_factory*.mjs
```

测试使用临时 SQLite、模拟服务、可控时钟及真实子进程退出；包括长等待、两个云项目与本地工作交替、重复领取/旧 lease、保存 task_id 后退出、完成后下载、退避上限、未知 POST 拒绝重投、暂停时间计量、重复迁移和真实 LangGraph 持久检查点重入。CPU 测试不调用真实付费服务或 GPU 模型；通过测试不代表真实云供应商的可用性或视频质量已验收。

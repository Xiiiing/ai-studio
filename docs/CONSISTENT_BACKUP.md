# 一致性备份与隔离恢复协议 v1

优化 9，分支 `feat/consistent-project-backup`，独立基线 `main@4aaece74ab54f38c125c564accc20f4134fb5bab`。本工具只创建数据快照和隔离恢复目录，不启动工作流、服务或模型。

## 范围：完整实例，而非拆出一张项目表

当前项目、队列、费用账本、系列版本/依赖共享 `studio.sqlite3`；LangGraph 用同一项目 ID 作为 `checkpoints.sqlite3` 的 `thread_id`；制作素材和系列参考位于 `production/`。因此 v1 采用 `complete_instance`：保留两个完整数据库和全部非排除生产文件，清单逐项目列出 ID、数据库 revision、production_revision、slug 与快照校验值。**不提供会遗失共享依赖的单项目表导出，也不合并两个实例。**

默认数据库位置为 `<root>/runtime/factory/`。使用自定义 `DRAMA_FACTORY_DATA` 的部署必须显式传 `--data-dir`；恢复后统一放入新根的 `runtime/factory/`，不修改保存的 task_id、回执或项目 JSON。需要两个已有数据库；缺少 checkpoint 数据库时不会临时创建一个空库来冒充完整备份。仅尚未建立检查点的新安装应先按正常业务初始化，不能用空库替代丢失数据。

## 一致性边界：必须安排维护窗口

调用者必须先通过既有暂停流程使制作到达安全边界，确认 worker、Web 修改接口、Legacy CLI、遗留渲染子进程、下载器及其他文件写者都已静止。**工具不会停止、重启或替换任何服务**，也不会把“队列显示暂停”当作文件系统已经静止的证明。

确认后才能显式运行：

```bash
python -m factory.backup create \
  --root /path/to/ai-studio \
  --output /path/to/backups/new-snapshot \
  --maintenance-window-confirmed
```

输出父目录必须已经存在，目标目录必须不存在，且必须位于源代码/数据目录之外。没有不安全覆盖、自动服务操作或自动确认开关。

执行顺序：检查已有 worker.lock 可独占；依固定顺序对两个已有 SQLite 文件分别建立 `BEGIN IMMEDIATE` 写入保留锁；**两个锁都成功后**才读取项目、账本与检查点。持锁期间用独立只读连接调用 SQLite Backup API，避免在自己的写事务连接上调用 backup 而得到 SQLITE_LOCKED。复制素材前后复核源文件集合/哈希；同时对目标全部媒体做回读校验，对两数据库的全部逻辑行和 BLOB 流式计算摘要，比较锁内源与副本，不只比较任务状态摘要。最后释放锁并写入完成清单。

这保证在**操作者确实阻止文件写入**的维护窗口内取得可核对的一组数据库与文件副本；不是两个独立 Backup API 调用天然构成原子快照。SQLite 锁本身不能阻止任意文件写入，前后哈希也不能抵御恶意进程修改后恢复原值的 ABA 行为。工具不修复源系统崩溃前已存在的语义不一致；关系审计检查项目/线程、外键和固定资产版本，不声称理解或重放所有工作流业务状态。

已有 worker 占锁、数据库忙碌、路径不安全、文件缺失、哈希变化、版本不支持都会拒绝。数据库锁等待为 0.5 秒，不杀占锁者；单库 Backup API 最长 300 秒。失败/中断保留当前新目录供诊断，没有 `backup-manifest.json` 就不构成完整可恢复快照；再次运行须选择另一个新目录，不覆盖失败产物。

## 数据、配置与凭据

| 分类 | 行为 |
| --- | --- |
| studio.sqlite3 | 全量复制项目、决策/事件、队列、幂等键、记忆、费用权威账本、系列表，不重算费用或混合币种 |
| checkpoints.sqlite3 | 保存 checkpoint 和 pending writes 的原始 BLOB；工具不加载反序列化器、不执行图、不恢复任务 |
| production/ | 全量常规文件，包括旧候选、旧交付、任务回执和系列共享参考；不按时间清理 |
| config/、环境变量、models/、envs/、.git/、运行锁/心跳 | 不打包；不采集配置或环境变量值，清单记录类别及重新配置要求 |
| production 内 `.env*`、runtime.env、factory.json、常见私钥文件名/扩展 | 默认排除并只列相对路径；若其他材料引用被排除文件，完整性审计会失败，不声称已完整保存该引用 |

备份目录创建为 0700，文件为 0600。**备份仍含原项目的私人剧情、业务回执和不透明检查点，不是自动脱敏器。** 本轮没有读取任何真实生产数据；使用前应检查历史业务数据是否曾错误地内嵌凭据。工具不会通过修改数据库或反序列化 checkpoint 来偷偷删改这些历史内容。应由操作者将备份放入受控/加密存储，不应提交 Git 或公开分享。该实现没有部署 restic、远程存储或密钥管理平台。

## 验证、预检、恢复、审计

```bash
python -m factory.backup verify /path/to/backups/new-snapshot
python -m factory.backup preflight /path/to/backups/new-snapshot \
  --destination /path/to/isolated-restored-data
python -m factory.backup restore /path/to/backups/new-snapshot \
  --destination /path/to/isolated-restored-data
python -m factory.backup audit /path/to/isolated-restored-data
```

`preflight` 不创建目标。`restore` 在全部预检通过后建立全新目录，拒绝已有目录（即使为空）、源目录及源目录内部、备份目录内部。每个文件独占创建并回读校验；完成时写原备份清单和 `restore-report.json`。重复恢复同一路径会失败。中断不删除半成品，不复用或覆盖；使用新目录重试。

校验包含完整文件集合、每文件 SHA-256/字节数、两数据库共同 snapshot_id 与各自哈希、Schema 描述/完整内容摘要、quick_check、外键、项目/线程关联、系列版本与引用。数据库副本在快照中使用 DELETE journal，完成快照里的额外 `-wal/-shm/-journal` 会被拒绝，避免未列入哈希清单的页覆盖主文件。原源数据库 WAL 模式和内容不被改写；Backup API 能包含源 WAL 中已提交的数据。

恢复**只写数据**，不复制可运行应用或私人配置，不启动 worker，不调用 `recover_jobs`，不触发轮询、提交或付费请求。原 queued/running/unknown 状态保持原值；恢复报告明确 `generation_started=false`、`automatic_resume=false`。未完成云任务的 intent_id、task_id、file_id、request_signature 和币种可见，完整请求意图与费用记录仍在原数据库和生产回执中。

在任何恢复实例投入运行之前，操作者须核查模型/路径/配置兼容性、人工处理未知云结果、确认不会与源实例同时提交制作，并遵守原收费审批与恢复协议。**不要把拥有一份恢复目录当作已经获准自动继续生成。** 审计命令用于尚未修改的恢复快照；正式恢复生产后数据自然会变化，不应再期待它与旧快照哈希相同。

## 版本与兼容性

清单格式 `ai-studio.consistent-backup` version 1。当前支持实际审查的 studio 表集合与 `remote_phases_v1`，SQLite user_version=0，以及 LangGraph checkpoints/writes 表；writes 的 task_path 列有/无两种已知结构均可保留。未知表/列集合、迁移标记、user_version 或备份协议拒绝，没有自动迁移/降级。

每库各自记录 schema_version，**不要求两个数据库的 SQLite schema_version 数字相等**。它们属于不同模式，数字相同也不证明一致。共同快照 ID、源写入边界、结构/逻辑内容摘要和文件哈希才是配对依据。原数据库结构 cookie 在 Backup API 副本中可能不同，记录的是实际副本值。

只读结构兼容不是所有未来应用语义的兼容保证；源 Git commit/dirty、备份工具 commit/dirty、SQLite/检查点读取依赖版本都记录下来，历史生成模型身份只从被保留的原回执读取，不用当前配置补造。没有 Git 信息的离线数据目录明确为 unknown。

手工 `cp` 数据库、没有完成清单的旧目录、未知版本备份均不能直接恢复。旧备份应保留，用与其实际格式匹配的旧程序做独立预检；本工具不自动猜测格式或覆盖新环境。代码升级不需要数据库 Schema 迁移；删除/回退工具代码不影响原数据库和已保存的快照。

## 真实上游源码与许可

| 固定上游 | 实际阅读 | 本项目适配与未采用部分 |
| --- | --- | --- |
| restic/restic `9da3b87b07706956e8dc5fd5cb259b99ec940f51` | `internal/data/snapshot.go`、`internal/restorer/restorer.go`、`internal/restorer/restorer_test.go` | 学习快照标识/内容引用、恢复目标、逐文件验证与失败退出；新建目录、文件清单和回读审计。未复制分块/去重/加密仓库/覆盖恢复/删除策略，避免新增独立存储平台 |
| langchain-ai/langgraph `a6731f436700c7d46a9224ccd3a0e0ce3a5bfdb5` | `libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py`、`libs/checkpoint-sqlite/tests/test_sqlite.py`、`libs/langgraph/langgraph/pregel/_retry.py` | 检查 thread/namespace/checkpoint/pending writes、提交边界、interrupt/retry 传播；保留原始BLOB与任务账本。未迁移运行时版本、反序列化外部快照或把重试当外部副作用可重复执行的保证 |
| SQLite Online Backup API、Python 3.12 sqlite3.Connection.backup | 官方 backup.html、c3ref/backup_finish.html、Python3.12 sqlite3 文档 | 使用标准库Backup API和显式双库写入保留锁；不把逐库成功描述成跨文件原子性，不用在线 cp WAL 主文件代替备份 |

固定源码：[restic snapshot](https://github.com/restic/restic/blob/9da3b87b07706956e8dc5fd5cb259b99ec940f51/internal/data/snapshot.go)、[restorer](https://github.com/restic/restic/blob/9da3b87b07706956e8dc5fd5cb259b99ec940f51/internal/restorer/restorer.go)、[restorer tests](https://github.com/restic/restic/blob/9da3b87b07706956e8dc5fd5cb259b99ec940f51/internal/restorer/restorer_test.go)、[SqliteSaver](https://github.com/langchain-ai/langgraph/blob/a6731f436700c7d46a9224ccd3a0e0ce3a5bfdb5/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py)、[SQLite Backup API](https://www.sqlite.org/backup.html)。官方文档检索日期2026-10-14，不伪造文档Git SHA。

已读取 restic BSD-2-Clause 与 LangGraph MIT LICENSE。全部新实现为本项目独立Python模块，未复制上游源码、测试或许可文件，没有新增运行依赖。本轮测试仅用临时数据库、测试自建的真实LangGraph中断图及合成/不透明媒体文件；不验证GPU、云服务或艺术质量。

# 云视频预算、估价与明确授权（优化 1）

## 范围和保证

仅在 MiniMax 云视频的**新生成 POST**前收费控制。本地 ComfyUI、配音、图像、已知云 task_id 的 GET 查询/下载不消耗新的云提交授权。没有接入第二套编排框架；沿用项目 SQLite、LangGraph 检查点和现有决策阻断/恢复流程。

估价在首帧和实际请求参数就绪时生成，包含模型、镜头数、本批次具体提交次数、供应商生成时长、时间线时长、分辨率、单次/总费用、来源及请求签名。一次批次列出一次明确的 take；**自动质检返修产生的另一个 take/提示词必须另行授权**，不是把 max_attempts 当作无限收费许可。

金额统一为整数 `*_micros`：1 CNY = 1,000,000 micros。没有供应商实时可靠计费数据时，价格为 `unconfirmed` 或 `estimate`，`billing_hard_limit` 永远为 false。限制的是提交次数与保守估价预留，不能保证供应商实际账单不超出。失败/未知任务不自动释放预留。已确认费用由出品人根据凭据录入，不伪装为已自动核对的供应商发票；高于估价时会阻止后续超额提交。

## SQLite 增量迁移

`store.initialize()` 以 `CREATE TABLE/INDEX IF NOT EXISTS` 增加下表，旧项目快照、既有 jobs/checkpoints/memories 不重写、不删除。连接明确使用 WAL + synchronous=FULL，预留事务使用 BEGIN IMMEDIATE。

| 表 | 内容 / 唯一性 |
| --- | --- |
| cloud_batches | project_id、生产版本、源参数签名、不可变逐请求 scope；内容哈希为批次 ID |
| cloud_authorizations | 每次明确授权的版本、次数/估价额度、未知费用确认、actor、时间；追加历史 |
| cloud_tasks | project_id、shot_id、attempt、take、provider/model、task_id/file_id、状态、预估/确认费用；intent_id 主键，项目/镜头/take 唯一 |
| cloud_cost_events | 每次人工账单核对的金额、币种、依据、actor、时间；追加审计历史 |

上线前由运维停止对应工作进程并备份 SQLite（包含 WAL 的一致性备份），使用新代码正常初始化即可。回滚旧代码时保留新增表，不执行 DROP；旧代码**不具备新授权保护**，回滚后不能直接继续云端生产。本次开发及测试仅使用隔离临时数据库，没有对正在运行的生产数据库执行迁移。

## 使用

1. 模型与连接 → 编辑视频连接：填写单次**估价**、币种和估价来源；未知时单价留空。修改模型/价格后保存，旧授权不再支持新的 POST。
2. 正常选择故事和画风。云视频实际请求就绪后项目阻断，进入项目的“云任务与费用”，检查具体镜头、提示词、参数签名和估价。
3. 明确授权本批次。未知价格必须勾选单独风险确认；已知价格填写累计估价额度。表单提交带项目 expected_revision，过期页面返回冲突，不能覆盖新版本。
4. 恢复只复用已有 take、task_id、file_id。超额、新 take、模型/制作参数变化会再次阻断；暂停/恢复/返修不会重置历史账本。

API（同源 loopback 请求，沿用 Host/Origin/CSRF 边界）：

- `GET /api/factory/projects/{id}/cloud-budget`：报价、授权历史、全部云任务与按币种的累计费用。旧项目 GET 接口响应保持原契约。
- `POST /api/factory/projects/{id}/cloud-authorize`：`batch_id`、`expected_revision`、`max_submissions`、`max_estimated_cost_micros`（未知价格可 null）、`confirm_unknown_cost`。完全相同的授权回放幂等；改值使用最新 revision。
- `POST /api/factory/projects/{id}/cloud-confirm-cost`：`intent_id`、`amount_micros`、`currency`、`evidence`。不混算币种，缺少核对依据拒绝。

配置示例（纯示例，不是供应商报价）：

```json
{"pricing":{"unit_cost_micros":null,"currency":"CNY","source":"尚未取得已核对的计价依据"}}
```

## 提交与恢复时序

```text
准备确定的首帧/提示词/take → 持久批次报价 → 出品人 CAS 授权
→ take 文件锁 → 请求字节校验 → 当前已保存模型配置校验
→ SQLite 原子检查签名/额度并预留 intent
→ fsync remote_task.json(status=submitting) → 唯一付费 POST
→ 保存 task_id 并 fsync → 账本观察 → 查询 → 下载 → 媒体回执
```

DB 已预留但还没写 journal 就退出：即使实际 POST 尚未发生，也按未知结果处理，不自动复投。POST 后响应丢失：保留未知状态，不自动复投。journal 已保存 task_id 但账本尚未更新：恢复时从原回执补齐，再 GET 查询/下载。相同镜头有未核对旧意图时，不能用另一个 take 绕过保护。原回执保留的连接身份用于恢复；不同模型不能悄悄接管旧任务。

`generate_video()` 的新任务必须提供由生产服务构造的 `before_submit` 边界；低层适配器没有默认授权。已有 journal 的查询/下载无需重新授权。测试中的显式 authorizer 仅是测试替身，没有生产配置绕过开关。

## 边界

审批只约束本仓库发起的请求；供应商控制台或别的客户端产生的费用不在账本内。授权的线性化点是事务成功预留；之后发出的暂停/配置变更不会撤销已经开始的 POST。未知提交必须人工结合供应商记录核对，系统不会猜测未收费并自动清零。旧项目已有任务首次恢复时惰性补录，未掌握的历史费用保持 null，不推算为零。

LangGraph 在恢复时可能重新执行节点开头，因此所有收费副作用都必须由本持久意图保护，而不是依赖进程内布尔值。设计参考：[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。

## 验证命令

```bash
python -m pytest -q tests/test_factory_cloud_budget.py tests/test_factory_cloud_api.py
python -m pytest -q tests/test_factory_*.py
node --test tests/test_factory*.mjs
```

测试包含无授权、未知价格确认、次数/金额不足、授权 CAS/幂等、版本变化、并发线程/进程、两个真实进程退出窗口、task_id 回执恢复、旧任务下载、暂停/恢复迁移、模型设置变化、HTTP 同源/CAS、前端金额与 HTML 安全检查。所有网络、费用、图片和数据库均为测试夹具；不调用真实付费 API。

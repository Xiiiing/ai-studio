# 团队工单与实际执行记录

`factory.team_tasks` 为现有 LangGraph 的六类非人工决策节点添加审计包装：研究、提案、剧本/分镜、视觉预览、制作/后期和返修。图节点名称、边、人工中断、worker 队列及 provider 账本不变。不增加第三方编排框架、数据库表或模型请求。

`contracts()` 返回负责人、审核人、成员、输入/输出和主路径前置依赖。依赖描述用于解释工作分工；实际可执行顺序、返修回路仍以 `build_graph` 为准，不能使用工单记录授权执行。

## 状态与证据

实际进入图节点前，在既有 SQLite 事务中保存 `running` 记录；原函数正常返回才记 `succeeded`，其含义固定为 `graph_node_returned_not_quality_approval`。审核未过、资源/云任务等待、暂停和执行失败分别记录为 blocked/waiting/paused/failed。制作节点只转交返修时记 waiting，不声称已经交付。记录不填充配置中的模型名；真实调用模型与回执仍由原 `/flow` 提供。

每个任务保留一个当前快照和单调尝试次数，历史转换追加到原 events 表。若恢复时上次仍是 running，先记录 interrupted，再开始新尝试；不会假定之前已完成或跳过原函数。run_id 围栏防止旧尝试覆盖新尝试，终态不可再次改写。命令/版本签名改变时，旧完成记录显示为 previous_run，不冒充当前版本。

签名绑定项目、需求、制作规格及该阶段命令/代次字段；不包含会在同一次调用内变化的生成产物、队列进度或当前配置。这是任务审计归属，不是媒体内容校验：精确模型/输入/文件 SHA 仍由原缓存、生产收据和验收系统负责。审计围栏不替代 GPU/worker lease，也不能撤销已发生的模型副作用。

`GET /api/factory/projects/{id}/flow` 增加只读 `task_board`；`GET /api/factory/workflow` 增加 `task_contracts`。旧项目无记录显示 pending，不扫描旧进度补造执行历史。这里只展示契约约定的审核角色，不表示其已独立运行或已批准。

## 验证与回滚

专项命令：`python -m pytest -q tests/test_factory_team_tasks.py tests/test_factory_state.py tests/test_factory_team_flow.py`。测试使用临时 SQLite、真实 LangGraph 中断恢复和 mock 工作函数，没有模型/付费调用。实际测试结果见 PR，不将本说明当成已执行证据。

回退本 PR 的图包装即可停止新增审计；保留原项目快照和事件中的 team_tasks 数据，无反向数据库迁移，无媒体或费用账本改写。本轮不部署、不操作现有服务。

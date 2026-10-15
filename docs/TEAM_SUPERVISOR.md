# 总制片代理：有界委托与人工终审

默认 `guided` 保留现有三个人工关卡。创建项目时显式传入 `team_mode: delegated`，并为 `llm_roles.executive_producer` 显式绑定启用的模型，才可委托总制片。旧项目缺少字段仍是 guided；新角色是可选项，不要求已有全角色配置补填。

## 实际执行

`story_decision` / `style_decision` 先创建原有持久化 gate，再请求总制片。故事必须具有当前代次的独立审稿通过记录，且候选自身 `eligible is True`。视觉必须具有当前剧本审核和预览完成记录；只考虑本项目、本方案目录中的预览，并用无跟随符号链接的有界读取核对 SHA-256。选中的 answer 由原 apply_story/apply_style 和 commit_decision 收据继续推进，不添加第二调度器或任意工具执行。

`final_decision` 的代理仅汇总已记录的信息和人工待查风险。终片始终调用原人工 interrupt。该模型不接收图像帧或音频；记录的 `basis` 明确为 `text_and_record_review_not_film_review`，不得解释为看过成片、独立媒体验收或平台审核。

## 控制与竞态

- JSON Schema 不允许额外字段；只能 select 既有候选或 escalate。不能选择不合格故事、重新提案、任意路径或 approve 终片。
- 单个 gate 最多发起一次代理决策调用；其底层 JSON Schema 修正重试仍遵循既有 provider 上限，不能将一次逻辑决策描述成必定只有一个 HTTP 请求。崩溃留下的 requested 状态表示结果未知，不再次发起决策；图会保留人工处理入口。新代次才有新授权范围。
- 决策收据已提交、图检查点尚未落盘时崩溃，恢复会消费现有收据继续原流程，不重复请求模型、不停在已回答的人工关卡。并发人工决定同样使用原收据推进。
- 模型调用不占数据库事务。返回后重新核对项目 revision、pending gate、显式模式、暂停状态、候选输入指纹及预览文件证据；任何变化都将代理结果记为 superseded。并发人工决定/暂停不被覆盖。
- 人工反馈被保留；代理理由不伪装成人工反馈。实际 answer 标注 actor 和 supervisor_receipt。
- 模型报错、绑定缺失、审稿缺失、文件变化或越权输出均回人工；异常文本不会写入代理记录，仅保留错误类型。
- 无新增预算或费用接口，没有自动终片批准、平台上传和无界返修循环。模型调用遵循已有连接权限和限制，本 PR 本身不调用模型。
- 显式绑定的总制片语言模型可能按供应商计费；启用 delegated 代表允许这些有界文本请求，不意味着其费用已纳入视频云账本。视频付费授权仍由原有人工门控制，本次开发测试不执行真实模型请求。

每个项目只保存 story/style/final 三条最新代理收据，历史在 events 中保留。requested_model 是请求配置，不冒充实际响应模型；原 `/flow` 的 LLM 回执仍展示历史实际模型。新 Prompt 单独版本化为 executive_producer/1.0.0，已有五类 Prompt 和默认版本不变。

## 使用示例

创建 API：`POST /api/factory/projects` 的既有 payload 中添加 `"team_mode":"delegated"`；通过既有模型管理页面显式分配“总制片代理”。缺少绑定时创建仍可用，但会安全停在人工选择关卡。

开发者和普通用户使用同一受控生产流程；该字段不是多租户或权限隔离实现。最终平台准入、内容备案、授权与 AI 标识仍由出品方核验。

## 验证与回滚

专项：`python -m pytest -q tests/test_factory_supervision.py tests/test_factory_state.py tests/test_factory_role_routing.py`。覆盖明确授权、候选白名单、Schema 越权、人工并发、暂停、失败不重试、预览哈希/跨项目/符号链接、原模型配置兼容和真实 LangGraph 自动抵达人工终审。均使用 mock LLM、临时数据库、CPU 小图，不包含真实视频质量验收。实际结果单独记录在 PR。

回退代码即可恢复原人工门；保留历史代理收据，不伪造人工批准。生产回退前需暂停新任务领取并核对在途 gate，本轮不执行生产操作。建议与团队工单 PR 一起集成验证；两者修改既有图的不同区域，可独立审查。

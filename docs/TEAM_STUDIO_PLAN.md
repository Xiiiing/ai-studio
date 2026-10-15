# 团队式 AI 短剧工作室升级方案

开发基线：`origin/main@6ba36606da2a54dc07311691ded8bdda949c7ace`。先规划，后在隔离 worktree 实现、测试、发布分功能 Draft PR。不部署、不重启、不修改生产数据库或自启，不执行真实收费生成或平台上传。

## 目标与现状

一句需求 → 总制片统筹 → 故事/视觉/后期团队 → 独立审核与返修 → 可核验成片交付包。现有主线已有研究、编剧、分镜、独立审稿、图像/视频、配音、字幕、剪辑、局部返修、预算授权、Series Bible、制作规格、成片验收、备份及版本化 Prompt。本轮补真实工单、总制片代理和统一交付入口，保留 LangGraph + SQLite、原生 JS 和 /legacy，不新建重复调度系统。

| PR | 实现范围 | 关键验收 |
| --- | --- | --- |
| A | 团队职责、输入输出/依赖、真实图节点任务记录 | 开始/返回/阻断/延期，过期结果围栏，恢复与旧项目兼容 |
| B，依赖 A | 可选总制片代理及 guided/delegated 模式 | 受限选择故事和视觉方向，独立审核不可覆盖，CAS、人工优先、失败回人工 |
| C | 只读交付检查和发布材料待办 | 复用成片验收，缺证据不得通过，工程交付和平台准入分开 |
| D，依赖 A/B/C | 中文团队指挥台与创建入口 | 工单、阻塞、交接、总监结论、交付状态，XSS/过期响应/浏览器回归 |

## 团队与授权

出品人负责需求、制作规格、预算、最终审片和发布；总制片代理统筹已授权的创意选择。故事组：研究/策划/编剧/分镜；视觉组：美术/人物参考/关键帧/视频；后期组：配音/对白/剪辑/字幕/混音；独立审稿和画面质检保留独立模型绑定及输入上下文。

总制片不得改变独立审核结论、提高预算、批准付费调用、批准终片或发布。模型输出是有界建议，必须经过代码候选白名单、版本围栏和原有审核门。节点返回、质量通过、人工批准和平台审核分别记录，互不冒充。工单记录是现有图的审计视图，不授予额外生成权限，也不代替 worker lease 或 GPU 互斥。

## 参考与取舍

- LangGraph Supervisor：https://github.com/langchain-ai/langgraph-supervisor-py 。README 推荐多数场景直接实现 tool-based supervisor；采用受控交接，不安装额外 supervisor 库。
- LangChain Subagents：https://docs.langchain.com/oss/python/langchain/multi-agent/subagents 。借鉴专业成员上下文隔离与结果汇总。
- CrewAI Processes：https://docs.crewai.com/en/concepts/processes 。借鉴 manager 职责，不引入 CrewAI/Redis/新队列。
- Toonflow：实际读取 `d2e94aca7b59659ff6b4ca15f03cf134ec7f0e1d` 的 `packages/teams/storyboardTeam/team.json`，借鉴 director/writer/reviewer 与 delegates/tools/knowledge 分离。独立实现，不复制第三方源码或 Prompt。

## 交付边界

工程目标：版本绑定的 MP4、字幕、封面、制作清单、模型/素材来源及审核记录。制作规格沿用 production_profile 和实际媒体验收，不把 9:16 等项目选择冒充红果统一硬性标准。

红果账户准入、当前上传规格、内容和剧集审核需要官方后台核对；不使用同名第三方站点作为官方依据。出品方须核验备案/许可、音乐/字体/声音/人物授权及 AI 显式/隐式标识。官方说明：https://www.cac.gov.cn/2025-03/14/c_1743654685896173.htm 。技术检查不是平台审核或合规认证。

## 后续完整产品阶段

全剧季级计划/分集钩子；真实媒体上的总监跨镜审片和返修单；多项目公平调度与阶段级 GPU 资源；真实模型样片的艺术、动作、声音、口型、一致性验收；商业部署的认证/多租户。开发者/用户视图不等于已经实现租户隔离。CPU/mock 测试不冒充艺术验收。

## 测试与回滚

使用隔离临时数据库、mock/CPU 媒体，执行 Python 专项与完整 Factory、原生 Node、真实 Chromium 模拟交互及 git diff --check，逐 PR 记录实际结果和未运行项，不相加分支成绩冒充集成。工单使用现有项目快照和事件表，无新增表迁移；旧项目默认 guided。回退代码保留历史事件和批准素材；delegated 项目生产回退前需暂停新领取并核对待办，本轮不执行该生产操作。

原 wkp Runner 写入响应超时，状态未知且未重放；转至在线 msi 的独立同基线副本继续开发，原工作区保持不动。

# API 与工作流配置

默认基址：`http://127.0.0.1:8787`。这是单机工作台接口；浏览器和 CLI 共用状态、队列和业务规则。

请求需使用正确的本机 Host；写请求使用 `Content-Type: application/json`，请求体不超过 64 KB。模型目录和工作流接口只接受当前本机源；不为任意跨站页面开放。

## 只读接口

| 方法与路径 | 返回 |
| --- | --- |
| GET `/api/health` | 控制服务版本与可用状态 |
| GET `/api/factory/overview` | 项目列表、队列、后台资源快照 |
| GET `/api/factory/console` | 制片总览；全量项目统计、有界最近项目、待办、实际运行和排队状态 |
| GET `/api/factory/projects/{id}/workspace` | 一致快照中的项目、人工待办、有效动作、执行图和版本 |
| GET `/api/factory/capabilities` | 有时间戳的资源观察；不等同于艺术质量验证 |
| GET `/api/factory/projects/{id}` | 项目状态、决策、预算、镜头与交付 |
| GET `/api/factory/projects/{id}/flow` | 当前分工、实际执行回执、输入输出与任务交接 |
| GET `/api/factory/models` | 模型目录、角色绑定、可编辑配置、当前配置版本 |
| GET `/api/factory/workflow` | 实际 LangGraph 节点/边、角色、决策点与制作接口 |

```bash
curl http://127.0.0.1:8787/api/factory/models
curl http://127.0.0.1:8787/api/factory/workflow
```

`models` 的 `profiles` 用于展示，`editable_profiles` 用于编辑后完整保存，`role_assignments` 是角色引用。不要只提交页面显示的几个基础字段而丢失原配置的高级参数。

视频配置独立返回 `video_profiles`（可编辑连接字典）、`video_roles`（`video_generator` 绑定）、`video_providers` 和 `video_role_options`。文本与视频共享同一个完整配置 `revision`。旧配置未声明视频目录时，兼容为现有本地 Wan 连接。

工作流导出是可检查的执行图说明；当前不接受上传任意图来替换 Python 节点代码。

## 连接测试

POST `/api/factory/models/test`：

```json
{"profile_id": "writer_local"}
```

只探测已保存的配置。返回 `status`、`models`、`configured_model`、`model_match`、`elapsed_ms` 和 `generation: false`。未保存的页面草稿不会偷偷作为新网络目标使用。

测试失败会在结果中给出稳定的错误原因；接口本身正常返回并不代表连接通过。检测不会生成文本、图片或视频。

对于 `model_discovery: "skip"` 的文本连接，返回 `status: "unverified"`、`model_match: false`，不请求模型目录。百炼默认使用此模式；它不代表服务离线，也不代表已证明在线。

视频配置预检使用 `{"profile_id":"minimax_example","kind":"video"}`。只检查已保存配置和密钥环境变量是否存在，返回 `status: "configured"`、`network_checked: false`、`generation: false`；不提交收费任务，不验证视频模型可用性。

## 保存模型目录

POST `/api/factory/models`：

```json
{
  "profiles": {"writer_local": {"provider": "llama_cpp", "base_url": "http://127.0.0.1:8090/v1", "model": "drama-writer", "context_tokens": 32768, "vision": true}},
  "roles": {},
  "expected_revision": "revision-from-the-latest-GET"
}
```

这是字段形状示意，实际应提交完整目录与角色绑定。未分配角色只有在有效旧版默认 LLM 存在时才能继续使用默认模型；正常工作台保存会保留全部角色。视觉角色须满足能力约束。

成功返回新目录和版本。旧版本、无效配置、缺失角色引用或禁用连接被角色引用时拒绝保存。默认只替换 `llm_profiles` 与 `llm_roles`；请求可以同时提交 `video_profiles` 与 `video_roles` 更新视频配置。两字段必须一起出现，并在同一次 CAS 下原子保存。旧客户端省略视频字段时保留当前视频绑定；无效视频配置会阻止整次写入。制作预算、GPU、质量策略等配置保留。

## 团队工作数据流

`GET /api/factory/projects/{id}/flow` 返回：

- `project_id`、`revision`、`assignment_mode: "orchestrator"`：对应项目与总控分工方式。
- `teams`：当前角色及模型绑定，包括编剧、审稿、分镜、视频、配音和剪辑等职责。
- `nodes`：角色任务状态、`inputs`、`outputs`、`executions`。执行项中的模型、时间和回执均来自已保存记录；缺失证据保持空值。
- `steps`：实际工作流步骤的当前业务材料。每项包含 `id`、`inputs`、`outputs`；材料包含中文 `label`、可选 `value`、经本项目路径检查的 `artifact_url`、`source_label` 和 `source_node`。材料状态区分 `available`、`not_recorded`、`not_generated`、`too_large`；保存的文件缺失时使用 `artifact_state: "missing"`。
- `edges`：输入输出交接关系，不替换实际 LangGraph 拓扑。
- `evidence`：回执读取范围、数量、截断或不可读取的说明。

`workspace.workflow` 和 `/workflow` 的 `production_graph` 增加 `presentation.stages`（五个展示阶段的 `id`、`label`、有序 `node_ids`）及 `presentation.branches`（返修节点与 `return_to`）。节点保留实际执行映射，并增加中文 `description`、`input_keys` / `output_keys`、`display_order` 与 `conditional`；v1 的独立视觉设计仅在重新设计时执行。此分组用于阅读，不改变执行图。客户端仅根据实际 `edges` 汇总阶段间连线与返修方向，不按排序补造连线；流动效果依据当前实际执行阶段控制，暂停和人工等待停止动效。

调用项保留已有字段，并增加 `artifacts`（回执/解析结果）和 `attempts[].artifacts`（实际存在的请求/响应）。文件使用 `label`、`kind`、`artifact_url`，只按需通过既有 `/media/production/factory-{id}/…` 接口读取，不把原文加入轮询快照。新图步骤及总制片调用在回执中保存 `execution_context: {project_id, stage, run_id}`；匹配当前有效工单或关卡记录时使用 `scope: "current_run"`，其他记录保留历史范围。缺少响应文件的失败请求仍可查看请求和回执。

当前任务状态取项目阶段和版本完成标记，历史回执标为 `scope: "project_history"`。修改当前模型绑定不会改写旧执行记录；原回执没有记录的 stage/generation 不会被推断出来。客户端刷新项目时同时刷新 flow，并确认 `project_id` 一致。

## 创建项目

POST `/api/factory/projects`，成功返回 202：

```json
{
  "brief": "原创的两位成年邻居悬疑故事，少量场景，结尾完成反转。",
  "title": "邻里悬疑",
  "format": "motion_comic",
  "duration_seconds": 60,
  "request_id": "my-first-episode",
  "budget": {"gpu_minutes": 120, "wall_minutes": 180, "max_attempts": 3}
}
```

同一创建请求的重试使用相同 `request_id`；不能用同一个 ID 改变创作参数。

新项目默认 `team_mode: "delegated"`、`workflow_version: 2`。总制片尚未配置时，项目仍可创建，界面明确显示人工接管原因；终片始终需要出品人确认。已有项目缺少工作流版本时按 v1 恢复，其协作方式不因升级被修改。

## 制作待办与有效动作

工作台从 `workspace.operator.attention` 读取结构化问题、候选、审核依据和 `actions`，从 `workspace.operator.actions` 读取项目控制操作。动作格式为：

```json
{
  "id": "provide_feedback",
  "label": "提交意见并重新审核",
  "inputs": [{"name": "feedback", "label": "修改要求", "type": "textarea", "required": true, "maxlength": 4000}],
  "impact": "把意见落实到当前故事或返修稿，重新独立审核",
  "enabled": true,
  "disabled_reason": null
}
```

字段类型包括 text、textarea、number、select、multiselect；选项由服务端提供。客户端只展示实际返回的动作，不根据错误文字推测重试方式。提交仍使用 `POST /api/factory/projects/{id}/action`，字段平铺到原有正文：

```json
{"action": "provide_feedback", "feedback": "明确 S03 人物选择的动机，保留其余故事。", "expected_revision": 12}
```

操作包括补充授权素材 `provide_authorized_source`（url 或 text 至少一项，并提供 rights）、切回原创 `switch_to_original`、修改需求 `revise_brief`、重新生成 `regenerate` 和提交反馈 `provide_feedback`。是否可执行以当前 descriptors 和服务端事务校验为准。`take_control` 在安全边界转为人工控制；`delegate_control` 只在允许委托且总制片可用时恢复代管。两者都不能扩大费用权限或批准终片。

故事重新提案使用 `choice: "__repropose__"`，全部否决视觉使用 `choice: "__restyle__"` 并提交 feedback，均绑定当前 decision_id。候选选择同时验证关卡代数和当前审核/产物版本；项目 revision 正确不能使旧关卡重新有效。已有受理请求的完全相同重放保持幂等。

CLI 同样使用动作契约：`./drama status ID` 列出有效操作；`./drama action ID provide_authorized_source --inputs-file SOURCE.json --expected-revision REV` 提交结构化输入。`./drama workflow --workflow-version 1` 可核对旧执行图。

总览 `/api/factory/console`（兼容别名 `/api/factory/overview`）支持 `offset`、`limit`、`attention_offset`、`attention_limit` 查询参数。offset 默认为 0，limit 默认为 100；offset 须为非负整数，limit 为 1 至 100，HTTP 数值参数最多 9 位。未知参数、同名参数重复或非法值均返回 400。

项目列表还支持可选 `q`（最多 200 个字符，去除首尾空白后，对项目名称、编号和需求分别做不区分大小写的子串搜索）及 `filter=all|active|attention|delivered`（默认 all）。active 排除 approved、cancelled、blocked、paused；attention 仅包含 `attention.blocking` 为真的项目；delivered 包含状态为 approved 的项目。先从全量项目中搜索、筛选，再按更新时间倒序分页；例如 `/api/factory/console?q=邻里&filter=active&offset=0&limit=20`。

`projects` 和 `attention_projects` 独立分页，分别返回 `pagination` / `attention_pagination` 的 offset、limit、total、has_more、next_offset。`pagination.total` 和 `bounded` 反映搜索、筛选后的项目数；`attention_projects`、`attention_pagination`、全局 metrics、运行任务和近期交付始终覆盖全量项目，不受 q/filter 影响。较早的阻断项目不会因不在最近项目页中而从待办入口消失。无搜索、筛选参数时保持原有返回语义。

## 人工决策

POST `/api/factory/projects/{id}/decision`：

```json
{
  "type": "story",
  "choice": "proposal-id-from-project",
  "decision_id": "pending-gate-id-from-project",
  "feedback": "保留钥匙线索，采用温暖结尾。"
}
```

`type` 为 story / style / final。选项和 `decision_id` 从当前项目读取。过期决策不会覆盖较新的待办；已接受的同一决策可以安全重放。

终审的 `choice` 为 approve 或 revise。选择 revise 时提供具体反馈，由后续流程处理；生成初版不会自动执行 approve。

## 暂停与恢复

POST `/api/factory/projects/{id}/action`，例如：

```json
{"action": "pause"}
```

```json
{"action": "resume", "feedback": "按现有分镜继续"}
```

暂停在可恢复边界生效。制作预算耗尽时，需要明确提高相应总上限；提高预算不删除已发生用量。

MiniMax 任务提交后持久化 `task_id`，生成成功后持久化 `file_id`。状态查询或下载失败时，恢复使用原任务及原执行连接，不重复提交。POST 结果不确定且未得到任务 ID 时，项目进入待处理状态，需要先核对供应商任务；不会自动创建另一笔任务。暂停本地等待不等于供应商已取消生成。

复杂的局部返修请使用项目详情页，或 `./drama revise --help`；它要求明确镜号和当前项目版本，避免旧页面修改了新一版剧本。

## 可用 CLI

```bash
./drama --help
./drama run --help
./drama choose --help
./drama revise --help
./drama models --help
./drama workflow --json
```

`./drama outputs PROJECT_ID` 和 `export PROJECT_ID` 列出当前已存在的交付文件；不渲染、不移动、不上传，也不批准成片。`models export` 仅导出模型配置和角色绑定。

## 制作团队任务账本（0.1.0）

项目团队读取与任务分页接口、状态语义和兼容行为见 [可介入的 AI 制作团队](TEAM_CONTROL_0_1_0.md)。团队读取不执行模型，也不将文件存在推断为审核通过。

## 整剧连续制作（0.3.1）

`POST /api/factory/dramas` 增加可选 `automation`，其余剧名、主题/剧本、集数、时长和路线字段保持原有契约：

```json
{
  "enabled": true,
  "episode_numbers": [1, 2, 3],
  "budget": {"gpu_minutes": 120, "wall_minutes": 180, "max_attempts": 3}
}
```

`episode_numbers` 必须明确列出授权集数（全剧即 `1..episode_count`），不自动扩大到依赖剧集。GPU 分钟、墙钟分钟和尝试次数沿用原制作预算，作用于每个制作单元；长集的总上限随场景拆分后的实际单元数计算，不是全剧总价。省略 `automation` 或设置 `enabled:false` 保留旧项目的手动确认与派发行为。

旧项目显式开启使用 `POST /api/factory/dramas/{id}/actions`：

```json
{
  "action": "enable_automation",
  "automation": {"enabled": true, "episode_numbers": [1, 2, 3], "budget": {"gpu_minutes": 120, "wall_minutes": 180, "max_attempts": 3}},
  "expected_revision": 12,
  "request_id": "enable-continuous-1"
}
```

相同请求重放幂等；新请求必须提供最新 revision。授权保存请求标识、出品人授权来源和时间；自动固化的规划与生成批次使用 `actor:automation`，不冒充人工确认。无匹配当前稿件的独立审核通过记录时，先重新审核；有未解决歧义时保留人工入口。已开工或完成的剧集及其配置、预算用量、成果和固定规划版本不被重新生成。

整剧快照增加 `automation` 和只读 `automation_state`。后者包括 `status`、`stage`、中文 `reason`、`episode_numbers`、`budget_scope:per_unit` 和 `attention:[{code,message,episode_number?}]`。状态为 disabled/planning/reviewing/paused/blocked/producing/awaiting_final/completed/cancelled。attention 区分规划审核/歧义、未授权依赖、剧集阻塞和配置错误；完整审核记录及剧集操作继续使用原字段和接口。读取快照不会派发任务或调用模型。

自动推进由持久 worker 在规划任务释放执行权后执行；重启补齐漏掉的衔接，确认、建立剧集、生成批次及入队在同一事务提交。关闭浏览器不影响执行。全剧暂停阻止后续派发，恢复沿用原授权；费用不足、未知供应商任务和已用尽的自动返修不会被后台反复重试。人工修改已开工项目的全剧规划前须全剧暂停并等待安全边界，修改稿重新独立审核，明确恢复后继续；已有剧集保留固定版本，差异通过 `affected_episodes` 展示，需定向返修的成果沿用原返修入口。任何自动化状态均不批准成片，最终通过仍由出品人显式验收。

规划阶段尚未建立剧集时，`actions` 的 `retry` / `resume` 可携带可选 `feedback`（不超过4000字的文本）。新的非空补充要求独立保存为 `plan_feedback` 并保留来源历史，原始主题与剧本不被覆盖；策划、分集编剧和独立审稿均显式收到该要求。要求改变时从新的有界创作轮次开始，不沿用旧稿的通过记录或只重复审核旧稿；相同要求的暂停恢复保留当前轮次。初始规划暂停后也可以提交修改稿：等待原任务停止后重新独立审核，连续制作继续保持暂停，显式恢复后才固化并派发。

`enable_automation` 也可在全剧暂停后的安全边界重新授权未完成剧集/场景单元的每单元预算。已开始的工作变更额度须先全剧暂停，且受影响的剧集、单元和规划任务均无运行中的 job；额度或尝试次数不得降低，新时间上限须超过该单元累计用量。仅更新未完成记录的预算与已捕获配置中的相应有界限制，保留累计支出、尝试历史、供应商任务编号、成功素材以及原规划/模型身份；awaiting_final、approved 与 unit_ready 成果不修改。长集总授权显示已完成单元原额度与未完成单元新额度之和。重新授权后继续保持暂停，不自动重试；出品人明确恢复才继续，仍受新的有限额度约束。


## 一句话整剧制作（0.3.4）

`POST /api/factory/dramas` 可选 `production_mode: "one_sentence"`，最少提供 `brief` 和 `request_id`。新模式固定执行配置、全剧额度，自动生成全部剧集及固定版本交付包；历史请求保持原行为。完整字段、默认值、证据边界及恢复语义见 [一句话整剧制作](ONE_SENTENCE_PRODUCTION.md)。

响应新增 `production_spec`、`whole_drama_budget`、`delivery_bundle`。`GET /api/factory/dramas/{id}/download?bundle={source_signature}` 下载已保存且核验哈希的固定包；缺少或不匹配的版本拒绝，普通下载及 `batch_id` 继续沿用原协议。

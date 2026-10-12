# API 与工作流配置

默认基址：`http://127.0.0.1:8787`。这是单机工作台接口；浏览器和 CLI 共用状态、队列和业务规则。

请求需使用正确的本机 Host；写请求使用 `Content-Type: application/json`，请求体不超过 64 KB。模型目录和工作流接口只接受当前本机源；不为任意跨站页面开放。

## 只读接口

| 方法与路径 | 返回 |
| --- | --- |
| GET `/api/health` | 控制服务版本与可用状态 |
| GET `/api/factory/overview` | 项目列表、队列、后台资源快照 |
| GET `/api/factory/capabilities` | 有时间戳的资源观察；不等同于艺术质量验证 |
| GET `/api/factory/projects/{id}` | 项目状态、决策、预算、镜头与交付 |
| GET `/api/factory/models` | 模型目录、角色绑定、可编辑配置、当前配置版本 |
| GET `/api/factory/workflow` | 实际 LangGraph 节点/边、角色、决策点与制作接口 |

```bash
curl http://127.0.0.1:8787/api/factory/models
curl http://127.0.0.1:8787/api/factory/workflow
```

`models` 的 `profiles` 用于展示，`editable_profiles` 用于编辑后完整保存，`role_assignments` 是角色引用。不要只提交页面显示的几个基础字段而丢失原配置的高级参数。

工作流导出是可检查的执行图说明；当前不接受上传任意图来替换 Python 节点代码。

## 连接测试

POST `/api/factory/models/test`：

```json
{"profile_id": "writer_local"}
```

只探测已保存的配置。返回 `status`、`models`、`configured_model`、`model_match`、`elapsed_ms` 和 `generation: false`。未保存的页面草稿不会偷偷作为新网络目标使用。

测试失败会在结果中给出稳定的错误原因；接口本身正常返回并不代表连接通过。检测不会生成文本、图片或视频。

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

成功返回新目录和版本。旧版本、无效配置、缺失角色引用或禁用连接被角色引用时拒绝保存。只替换 `llm_profiles` 与 `llm_roles`，不覆盖制作预算、GPU、质量策略等配置。

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

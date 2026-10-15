# 逐镜制作工作台（优化 4）

## 使用

项目 → 镜头制作台：左侧选择镜头，右侧查看剧情目标、人物、资产、提示词、时长、镜头修订和生产版本。候选按图片/视频展示，包含生成参数、模型、评分、错误与已记录云费用。每类最多显示最新 40 个候选；截取或损坏回执明确提示，不删除历史。

先暂停并等待执行器结束当前步骤，再修改或选片。只有通过技术与所需语义质检、文件完整性验证、且属于当前参数的候选可选。勾选确认后，人工选择写入项目，优先于自动选片；不覆盖候选文件。参数或模型后来变化时，旧人工选择会阻断并要求重新确认，不会悄悄改成其他候选。

第一阶段支持 `prompt`、`motion_prompt`、`camera_motion` 及 API 的 `character_states`（wardrobe/held_props/position/story_state）。镜长、分辨率、模型等未开放的参数只读；未知字段拒绝。勾选明确重制可以在参数相同时创建新的 image/video 渲染版本；未勾选的无改动提交拒绝。修改首帧会影响其视频和 reference_shots 的传递依赖；仅改视频提示词不重生成首帧，无关镜头复用。

保存后项目保持暂停。点击恢复制作继续原 LangGraph 路线，重新进入终审；新云请求仍必须通过费用授权。已完成的成片保存在 delivery_history，旧 take 和账本不清除。故事、画风和剧本评审门不能被单镜操作绕过。

## 数据模型 / 迁移

无需新增数据库表。仅在首次操作时给项目快照追加：`production_edits` 审计、`shot_selections[shot_id][stage]` 人工固定回执、`shot_revision`、可选逐阶段 `render_revisions`。沿用优化 3 的 production_edit_revision 持久重入机制；读取旧项目不改写它们。新回执增加 plan_signature、镜头/制作版本；旧候选仅在与当前选定素材的 source_signature 一致时允许选择，执行时仍检查完整模型/输入签名。

所有修改在 SQLite BEGIN IMMEDIATE 内校验 `expected_revision` 与当前无 running job；未知或未完成云意图阻止编辑及新选片。结果丢失后不要盲目重复提交，先刷新核对状态；旧 revision 不能创建第二次编辑。

## API

- `GET /api/factory/projects/{id}/shots`：项目版本、逐镜计划、候选和回执错误。
- `POST /api/factory/projects/{id}/shots/{Sxx}`：`action=edit`，`expected_revision`，`changes`，可选 `regenerate`/`stage`。
- 同一路径 `action=select`：`expected_revision`、`stage`、`take`、`confirmed=true`。

不接受文件路径或外部 URL 作为选片来源。所有路径检查所属 factory_owner、限定目录、类型及校验值，输出文本经过 HTML 转义，URL 使用既有安全处理。已知不可信回执不可成为任意文件读取入口。

## 前端刷新与验证

React 按项目和镜号保持组件身份，刷新状态时不替换相同 source 的视频。编辑草稿固定最初的 CAS 版本，版本冲突时保留输入并要求刷新核对。旧 UI 已移除，`/legacy` 返回 410。

```bash
python -m pytest -q tests/test_factory_shot_workbench.py
python -m pytest -q tests/test_factory_*.py
npm ci --ignore-scripts
npm test
npm run build
FACTORY_TEST_PYTHON=.venv/bin/python npm run test:browser
```

UI 单元测试用于组件行为验证，不等同于真实浏览器视频播放验收。真实浏览器验收由独立 browser smoke 命令覆盖。CPU 测试不下载模型，不调用收费服务。

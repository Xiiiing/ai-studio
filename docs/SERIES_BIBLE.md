# Series Bible 与版本化视觉资产：最小数据模型

## 实施前模型与迁移约定

本模块复用项目的 SQLite，不迁移或重写任何历史项目，不引入向量库或新的 Agent 编排。

| 模型 | 主键与主要内容 | 不变量 |
| --- | --- | --- |
| series_bibles | series_key + version；世界观、视觉规范、characters、scenes、props；创建时间 | 内容版本追加，旧版本不可覆盖；expected_version 校验最新版本 |
| series_assets | asset_id + version；series_key、kind、entity_id、描述、状态、revision、媒体 receipt、质检、审批 | 同一 asset_id 的系列/实体/类型稳定；每次换参考图新建版本；approved 版本不可再编辑 |
| series_dependencies | project_id + shot_id + asset_id；固定 version、sha256、role | 一个镜头只引用已批准且校验通过的确切版本；用于影响分析，不自动追随最新版 |

角色用稳定 `character_id` 标识。Bible 中 identity_constraints 表示身份外形约束，default_wardrobe 仅是默认值；逐镜 `character_states` 分别描述 wardrobe、held_props、position、story_state，不改写身份定义。场景、道具同样有稳定 entity_id。

素材状态流程为 planned → generated → reviewed → approved。登记媒体时保存不可覆盖的独立参考文件，审查阶段执行技术校验，最终批准需要明确人工确认；文件缺失、校验值不符或验证失败都不能批准/复用。自动生成或通过技术检查本身不等同于正式素材批准。

旧项目没有系列绑定时继续走原 cast / assets / receipts 路线。新项目填写 series_key 时可固定当前 Bible 版本；没有正式 Bible 的旧 series_key / memories 仍可使用，不自动升级为已批准素材。已有镜头可通过工作台显式引用同系列批准资产。

资产新版本不会原地更换历史项目的参考图。影响查询返回仍引用旧版本的项目/镜头；用户以当前项目 revision 显式绑定新版本。只有这些镜头的资产依赖参与其缓存签名，且实际生成回执包含 asset_id、version、sha256。累计预算和旧 take、已批准原文件保留。

数据库变更为 `CREATE TABLE IF NOT EXISTS` 的增量表与索引。部署前对 SQLite（含 WAL）做一致性备份，在维护窗口启动新代码执行 initialize；开发验证只操作临时 SQLite 和隔离生产目录，不触碰运行实例。回滚可保留新增表，旧项目无须批量转换。

## 工作台使用

创建项目时填写系列标识，进入“系列资产”页。首次使用先编辑并保存正式 Bible JSON，再选择角色、场景或道具建立 planned 资产。登记来源只能选择本项目已有的图片回执，不接收任意路径、下载地址或文件内容。随后执行技术验证，审看图片并勾选明确确认后，才成为 approved 参考。

已批准资产可以在同系列的另一剧集中固定到指定镜头；角色资产还需选择本集实际出现的 cast 角色。新版本按钮只创建新的 planned 版本，不修改旧版本图片。页面列出直接引用和 reference_shots 的传递依赖，不会把无关镜头都标记为失效。

引用修改必须发生在暂停或人工决策阶段且没有正在执行的 job。选定故事、画风并通过剧本评审的项目，引用变更后保持暂停，点击原“恢复制作”重新进入现有 LangGraph 生产节点。已失效的终审门被 superseded，旧交付存入 delivery_history；原选故事/定画风门不能被绕过。未选定画风的项目继续原人工决策流程。旧素材、历史 take 与费用账本不被清除；受影响镜头存在未核对云任务时禁止改绑。

每个镜头的固定资产 ID、版本和 SHA-256 进入素材签名、attempt 与实际结果 receipt。系列素材的技术验证只检查完整性、解码和基础图像问题，不保证角色相似度或艺术效果；人工批准不能由自动化技术分数替代。

## API

`GET /api/factory/projects/{id}/series` 返回最新设定、本集固定设定、资产版本与验证记录、可登记来源、固定引用及受影响镜头。最多显示最近256个资产版本，截断时明确返回 truncated，历史版本与依赖不删除。

`POST /api/factory/projects/{id}/series` 统一使用 JSON，每次要求 expected_revision：

| action | 附加参数 |
| --- | --- |
| save_bible | expected_version（首次0）、document |
| plan | kind、entity_id、expected_version；同资产升级还提供 asset_id |
| register | asset_id、version、expected_asset_revision、source_key |
| review | asset_id、version、expected_asset_revision |
| approve | asset_id、version、expected_asset_revision、confirmed: true |
| bind | asset_id、version、shot_ids、role |

沿用现有 loopback Host/Origin、JSON大小和文件访问约束。前端草稿固定最初的项目/资产/Bible修订号；自动刷新不会把陈旧草稿伪装成新版本。冲突保留草稿，不自动再次提交。

## 边界与测试

Bible 每系列最多16名角色、32个场景、32件道具；这只是资产组织上限，不改变当前单集生成规格（可配置生成规格由优化5处理）。每镜最多6个系列引用。参考图为64–4096像素PNG/JPEG/WebP，最多8MiB。只从已有素材登记；没有新增自动角色生成系统、向量检索或图片上传服务。

```bash
python -m pytest -q tests/test_factory_series.py
python -m pytest -q tests/test_factory_*.py
node --test tests/test_factory*.mjs
```

测试包括跨集固定复用、版本变化、未批准/验证失败/校验值篡改、源工程归属、路径隔离、资产与项目CAS、场景/道具绑定、未完成云任务阻断、旧格式兼容、真实制作缓存只失效关联镜头、实际LangGraph终审检查点重入、HTTP同源防护和前端草稿/确认交互。

设计参考：`zenstory-ai/drama-skills` 的系列创作设定组织，以及 `HBAI-Ltd/Toonflow-app` 的人物、场景、分镜和素材组织；本实现保留本项目的单一 SQLite/LangGraph 架构，不复制外部工作流。


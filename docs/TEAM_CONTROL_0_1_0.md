# 0.1.0 · 可介入的 AI 制作团队

本轮由 Issue #37 管理，沿 UI #35 的成果开发，合并顺序为 UI #33、#35，再依次合入三个团队 PR。模型接入 #34 不在本轮差异中。本文描述源码与隔离验证，不代表已合并或部署。

## 第一阶段：任务账本与团队指挥台

新增七部门、稳定岗位身份和持久化制作任务；模型配置与历史实际执行身份分别表达。任务详情展示实际输入、产物、独立审核、采用状态、依赖与尝试。团队读取接口不启动模型、不写任务、不补造旧历史。

新建 v2 项目启用任务账本；已有 v1/v2 无控制标识时保持既有执行和观察能力。工作流阶段仍由 LangGraph 执行；生产适配器在真实图像、视频、审核、配音、对白时长、剪辑与技术检查边界记录任务。已有有效素材标为复用，不生成执行次数或新时间。配音共享一个推理批次时逐句记录实际成果，并明确共享批次边界。

当前阶段保持原有执行顺序。生成完成、独立审核通过与人工采用分别表达。迟到的已失效尝试保存历史成果、模型与外部回执，但不能覆盖当前任务。

### 读取接口

- `GET /api/factory/projects/{id}/team`：部门、成员、任务概况与真实制作动态。
- `GET /api/factory/projects/{id}/tasks`：`offset`、`limit`（1–100）、`department`、`status`、`shot_id` 服务端筛选与分页。
- `GET /api/factory/projects/{id}/tasks/{kind:scope}`：稳定任务身份、版本与最近尝试。

SQLite 增加 `team_control`、`team_work_items`、`team_unit_attempts`。备份支持新表，仍接受尚无团队表的旧数据。阶段一不提供未接入执行的控制按钮。

### 验证

使用独立 SQLite、真实 loopback HTTP、CPU 合成图像/WAV/视频及模拟模型，不调用 Wkp 或收费接口。专项测试覆盖只读无写入、旧项目、稳定身份、版本隔离、迟到回执、模型来源、生产入口与缓存复用。UI 单元测试、类型检查和构建通过；Chromium 操作桌面/手机浅深主题，验证筛选、任务/依赖/动态定位及刷新保持媒体播放。

浏览器合成界面证据位于 `runtime/browser-report/team-console-{light,dark}-{desktop,mobile}.png`，CI 可保存报告。实际模型与生产服务器验证尚未进行。

以下是浏览器合成 fixture 的界面截图，内容不代表生产项目或真实模型结果：

![桌面团队指挥台](images/team-0-1-0/console-light-desktop.png)

![手机深色团队指挥台](images/team-0-1-0/console-dark-mobile.png)

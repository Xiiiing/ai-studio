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

## 第二阶段：介入与成果保护

在任务或成员入口选择明确任务，填写新要求或选择重新生成；可预览依赖影响、指定保留成果，并选择自动继续或完成后等待。指令与任务操作使用独立 `control_revision`、任务 `generation` 和幂等编号，普通进度刷新不会使编辑失效。相同指令重试不会重复登记或应用。

指令立即阻止新的旧安排派发；当前本地/供应商调用完成后保存尝试文件、原始成果和费用，再切换。已提交的异步云任务继续按原 ID 查询和下载，不能通过新参数重发。受影响旧成果不可成为当前选片。worker 释放旧租约后应用指令，崩溃恢复仍使用原队列与供应商身份。

任务支持暂停/恢复、锁定/解锁、完成后等待/确认继续。锁定绑定具体成果与制作输入；原有镜头、素材和 CLI 修改也通过同一写入保护。已审核剧本镜头、对白正文、已固定场景参考可分别锁定。已通过的镜头或配音保留原版本，返工按实际依赖传递，成片保留历史并重新终审。

直接图像/视频要求进入对应提示词；配音要求进入 Qwen3-TTS 的逐句情绪指令，显式同文本重做使用新的受控 seed。云端配音同文本重试有独立请求身份，仍须原费用流程授权；当前云端接口不支持的逐句情绪修改会交回处理，不伪称生效。创作要求进入实际模型请求与缓存签名，明确重做不会误用旧缓存。

团队控制启用时按逐句边界执行配音，保证单句介入不重做其他句。已有远程调用、费用不重置；此前无控制标识的 v2 项目仅在安全边界显式启用，导入成果先核对实际文件与 SHA-256。v1 不改写检查点。

### 控制接口

- `POST /projects/{id}/team/actions`：`enable`。
- `POST /projects/{id}/tasks/{task}/actions`：`pause`、`resume`、`lock`、`unlock`、`hold_after`、`release`。
- `POST /projects/{id}/interventions/preview`：只读依赖影响与锁定/保留冲突。
- `POST /projects/{id}/interventions`：登记任务或成员指令；`GET` 支持分页读取。
- `POST /projects/{id}/interventions/{instruction}/dismiss`：撤回尚未应用的指令。

以上路径均以 `/api/factory` 开头。任务动作要求 `request_id`、`expected_control_revision`、`expected_generation`。指令额外包含明确的 `target_type`/`target_id`、`task_ids`/`task_generations`、`instruction`、`operation`、`preserve` 和 `after`。服务端返回可执行动作，UI 不提供未接入的控制。

第二阶段尚不启用部门/总制片的模型重排入口；第三阶段完成结构化安排与实际调度后再提供。

第二阶段隔离验证：全量 Python 1523 项通过；检查发现的异步云任务范围隔离已修复，包含该场景在内的 113 项团队/云端专项全部通过。UI 全量 160 项、类型检查与构建通过，7 项相关 Chromium（包含真实 Python HTTP 项目页）通过。开发自查检查了以下模拟介入界面：

![桌面介入要求](images/team-0-1-0/controls-light-desktop.png)

![手机返工影响](images/team-0-1-0/controls-dark-mobile.png)

# 通用生产验收 v2

工作包 8，独立分支 `test/production-acceptance-v2`；开发基线 `4aaece74ab54f38c125c564accc20f4134fb5bab`。本分支不依赖未合并的 HTTP 工作包 7。

## 命令与副作用边界

在已停止修改、但不必停止服务的项目版本上进行只读验收。以下示例 ID、版本和 SHA 是需要替换为已保存回执值的占位变量，不会自动选择最新成片：

```bash
envs/factory/bin/python -m factory.acceptance \
  --project-id "$PROJECT_ID" \
  --database runtime/factory/studio.sqlite3 \
  --production-revision "$PRODUCTION_REVISION" \
  --media-sha "$EPISODE_SHA256" \
  --output runtime/factory/acceptance
```

默认数据库是现有 `factory.config.DATABASE` 路径；不会加载当前模型配置来补造历史模型身份。`--root /path/to/project-root` 指向媒体所在根目录；配合 `--project-json /path/to/frozen-project.json` 可以验收已冻结的项目快照，不必打开数据库。JSON 快照必须包含准确的项目 ID，不能把其他项目的快照当作指定项目。

数据库通过 SQLite URI `mode=ro` 打开，显式单次读事务内读取项目及云任务记录，随后关闭连接；不调用 `store.initialize/update/action`，不迁移、不 checkpoint、不启动 worker、不轮询或提交云任务、不查询 systemd。SQLite 的只读连接不意味着操作系统永远不会访问 WAL/共享内存元数据；本工具承诺不执行 SQL 数据变更，不是文件系统级隔离。

输出路径不能在 `production/` 中，也不能通过符号链接访问。每次建立独立的 `<project_id>-v<production_revision>-<run_id>/` 目录，不覆盖其他项目或同一项目的旧报告。媒体只读；缺失文件不会自动生成或修复。`production_revision` 选择器不匹配时输出失败证据，不会回退验收别的版本。

退出码：0 表示技术检查通过并导入了与本次版本/媒体绑定的人工通过记录；2 表示修复需求或输入阻断；3 表示仍需人工审片。单纯技术通过不会返回“艺术质量已认证”。原位置参数 `python -m factory.acceptance PROJECT_ID` 仍可用，但不再运行历史部署/样片检查。

## 报告与一致性边界

报告继续使用已有 `ai-studio.film-evaluation.v1`、既有七个维度和 `evaluation/golden.json`。通用验收附加 `acceptance.schema=ai-studio.production-acceptance.v2`、协议版本 2；没有另建评分体系，也没有调整原评分阈值来消除失败。

每次输出 `report.json`、`metrics.csv`、`human-review-template.json`、`summary.md`、`failures.json` 和最后写入的 `export-manifest.json`。完成清单逐文件记录 SHA-256 和字节数；缺少清单表示导出未完成，不能用于正式基线比较。失败证据包含项目、生产版本、镜号（适用时）、媒体签名、具体源文件校验和失败原因。

可追溯字段包括：执行工具的 git_commit 与 git_dirty、参与验收的实际源码校验值、输入快照签名、记录的 project revision / production revision、解析后的制作规格及签名、现有调用回执中的配置版本与模型身份、导入评估文件签名。缺失历史配置/模型记录明确写 `unknown`，不会用当前配置填补。工具运行时没有调用模型，`model_execution` 和 `generation_submission` 均为 `not_run`。

验收前后比较项目快照签名，并在解码结束后复核已收集的媒体证据哈希。检测到并发项目变更或持久文件变更时判失败。**这不是 SQLite、多个数据库和文件系统的原子快照，也不能抵御恶意本机进程在两次检查间替换再恢复文件。** 对运行中的制作项目应先通过现有安全暂停流程等待稳定版本；跨数据库/素材的一致备份属于工作包 9，不在这里伪造保证。

## 检查范围

| 对象 | 实际检查 | 不作出的推断 |
| --- | --- | --- |
| 版本与归属 | 精确 project_id、生产版本选择器、目录 owner 回执（存在时）、主成片路径及 SHA | 不把旧版本当最新，不自动迁移媒体 |
| 制作规格 | legacy15、compact6、ensemble10 的镜号集合、数量、录入时长及合法 edit_plan 裁剪参数 | 不因项目自报时长就认定解码成功 |
| 剧本与分镜 | 已存在的 script/episode 锁定字段一致；允许分镜导演只改变制作视觉字段；运行对白 ID/角色与剧本对应 | 不将语义变化包装成视觉细化 |
| 配音适配 | 原配音、处理后对白的路径/哈希、实际解码时长、测量回执、镜头时间窗口 | 不把测试正弦波当真实 TTS，不运行 ASR |
| 字幕 | 解析合法性、顺序、文本、镜头归属、实际对白窗口；支持一句对白拆成多条字幕 | 不把 Schema 通过当台词自然或表演合格 |
| 交付 | 原 manifest 与项目快照完整一致；timeline/audio_edit 时长及来源对应；成片、清洁版、字幕等原交付检查 | 不在验收中重新剪辑或覆盖批准成片 |
| 安全 | 生产相对路径、项目归属、符号链接拒绝、有界 JSON/数组/音频长度 | 不是多租户鉴权或对恶意本机管理员的防御 |
| 外部与人工评估 | 只导入已有 hash-bound VBench/人工记录；仍使用原协议校验器 | 不因模型未运行而填通过；不自动代表制片人批准 |

现有 voice-fit 可能对运行对白进行压缩而不回写已审剧本。对白 ID/角色变化是失败；保留角色/ID 的文本或起始点适配明确列为 `review_needed`，等待本次媒体绑定的人工确认，不自动认定语义等价。旧项目缺少可证实的测量/剪辑回执会显示失败或未运行，不伪造新回执；项目数据本身保持不变。

实际 `AudioEditorMixin._mix_audio()` 的 `audio_edit.json` 只记录逐句时长，没有总 `duration_seconds`。验收不要求补写历史字段：总时长仍从真实 soundtrack 解码和 timeline 校验，audio_edit 如显式记录总时长则严格核对。回归直接调用原 CPU 混音器生成凭据再验收，避免只用手工夹具验证不存在的格式。净版与主版等文件都带路径和哈希证据，在解码后再次校验；报告 revision 必须为受限整数，不允许进入路径的自由文本。

保留原媒体解码器、24 fps 制作契约和既有质量阈值；没有增加 GPU worker 或云模型。原始视频长于剪辑实际使用范围的复杂历史 reedit 仍受原 `factory.quality` 校验语义约束，未在本工作包加入 source_range 时间线模型；更完整的版本化剪辑属于工作包 12，不能把该场景标为已全面验收。

## 人工记录、外部结果与回归比较

先运行验收得到 `human-review-template.json`。实际审片后由制片人填写 reviewer、reviewed_at、decision 和全部七个维度证据，再导入：

```bash
envs/factory/bin/python -m factory.acceptance \
  --project-id "$PROJECT_ID" --production-revision "$PRODUCTION_REVISION" \
  --media-sha "$EPISODE_SHA256" --human-review /path/to/completed-human-review.json \
  --baseline /path/to/previous-complete-export/report.json
```

人工记录绑定项目、生产版本、核心媒体、对白及全部交付/配音回执的签名。版本或媒体不匹配会生成失败项，不沿用旧批准。该记录是操作者提供的审片声明，不是本工具签发的真实人类身份或实际观影证明；测试中的人工记录只是模拟输入。

`--vbench-report` 仅导入现有适配格式；不会启动 VBench，也不会自动下载 DINO 等模型。真正 GPU/云端质量验证需在其他显式授权流程执行，付费视频仍只能经现有费用审批和权威账本，不通过验收 CLI 发起。

比较要求同一项目、相同评分协议、golden、制作规格、评价代码及验收代码签名；生产 revision 和媒体签名可以不同，但报告会明确记录两边值。不同评价实现、缺失版本、不同项目及历史专项报告均返回 `comparable=false`，不伪称回归通过。检查项按镜头/来源/证据路径/对白身份建立键，移除、增加与状态变化都会报告，不再只看候选仍保留的条目。导入 v2 基线先校验完成清单及全部文件；哈希是完整性校验，不是防止拥有全部文件写权限者伪造证据的认证。

旧 `autonomous_factory.acceptance.v1` 文件可读，但缺少兼容评分协议与媒体绑定时不能作为正式比较基线。旧 `film-evaluation.v1` 保留默认接口和历史单字幕逻辑；有实际 timeline 字幕分段证据时使用严格的分段对应校验。

## 历史专项检查

固定样片、固定服务名及历史全局报告写入入口已删除。验收统一使用上面的通用只读命令；历史报告可以作为资料保留，不作为当前版本的验收结果。

## 上游源码、适配与许可

| 上游固定版本 / 实际源码 | 学到的机制 | AI Studio 的选择与未采用部分 |
| --- | --- | --- |
| zenstory-ai/drama-skills `c2426e03c0e7722bebcc6a488b6658dc38c65ac3`：`evaluations/README.md`、`evaluations/content_quality_gate.py` | 冻结输入、来源/版本/配置/报告/收据绑定；真实运行与模拟回归分层；开发集、一次性 holdout，不能挑选最好样本 | 绑定本项目已有版本、媒体和评估代码；拒绝不完整基线，不借技术回归宣称艺术提升。未移植其盲评调度和技能运行器，创作 A/B 属于工作包 10 |
| 同 commit：`tests/test_content_quality_gate.py` | 缺失 replicate、伪造案例元数据、跨样本复用拒绝；逐案例回退不能被宏平均掩盖 | 增加来源/版本/缺文件/哈希/移除检查/中断输出测试；不使用其测试夹具分数声称本项目质量提升 |
| Vchitect/VBench `fd18b3d055cb0fc6f066ca90fe2c3c8cbb698490`：`vbench/__init__.py` | 明确输入模式、维度、重复样本与逐视频结果；真实模型评估需显式初始化 | 继续已有 VBench 结果导入协议；不把技术验收当完整 VBench，不引入其模型加载、分布式运行和权重下载 |
| 同 commit：`vbench/subject_consistency.py` | 归一化图像特征与首帧/前帧相似性、逐视频及按帧数聚合；执行依赖真实特征模型 | 仅学习结果可追溯与维度边界；不复制算法代码，不用 CPU 色块伪造角色一致性成绩 |

源码链接固定到上述 SHA：[drama gate](https://github.com/zenstory-ai/drama-skills/blob/c2426e03c0e7722bebcc6a488b6658dc38c65ac3/evaluations/content_quality_gate.py)、[drama tests](https://github.com/zenstory-ai/drama-skills/blob/c2426e03c0e7722bebcc6a488b6658dc38c65ac3/tests/test_content_quality_gate.py)、[VBench lifecycle](https://github.com/Vchitect/VBench/blob/fd18b3d055cb0fc6f066ca90fe2c3c8cbb698490/vbench/__init__.py)、[subject consistency](https://github.com/Vchitect/VBench/blob/fd18b3d055cb0fc6f066ca90fe2c3c8cbb698490/vbench/subject_consistency.py)。

已阅读 drama-skills 的 MIT LICENSE 和 VBench 的 Apache-2.0 LICENSE；独立实现接口适配与通用机制，未复制第三方源码、测试、语料或媒体。上游特定小说样本另有用途限制，没有下载或转入本项目。没有新增大型依赖。

## 部署与回滚

无数据库迁移，无生产配置变更，无部署或服务重启。用户升级代码后可直接使用新 CLI；它只在指定非生产媒体输出目录追加报告。回退代码不需回退数据库；新报告保留为只读历史证据，旧代码无法验证 v2 的附加契约时不能将其解释为兼容通过。真实 GPU、云端、艺术质量、历史生产样片及全工作流实际制作均未运行。

# 可审计成片评估（优化 6）

## 指标定义与运行边界

评估按四类证据分离：确定性媒体检查；带时间点/镜头证据的VLM主观判断；可选离线视频指标；当前媒体版本的人工审片。未运行、缺失、不可用和通过是不同状态。技术通过不能替代剧情、身份、道具、表演或艺术质量通过。

`evaluation/golden.json` 固定七个维度、正反控制、缺陷类别和返修建议。CPU CI只用本地合成媒体与模拟评估器验证协议及控制，不把合成图形当成真实模型画质样本。真实生成样片需要明确指定文件校验值、模型与配置版本及独立人工结论，不能由模拟测试自动补成通过。

报告直接输出JSON/CSV，不部署Langfuse；项目、镜头、候选版本、模型身份、耗时、重试、估价/确认费用及评估协议版本相互关联。未知历史费用或模型留空，不用当前配置补造历史。

时序抽样采用均匀时间点、端点与可选切点邻域，保留实际解码时间和帧号；逐镜默认8个时间样本，不再只有首中尾。跨镜检查使用相邻或共享角色的实际镜头样本，并以固定系列资产作身份参考。有限抽样仍可能遗漏短暂错误，不能推断完整动作或口型必然正确。

## 默认离线评估

```bash
python -m pip install -r requirements-factory.txt -r requirements-evaluation.txt
python -m factory.evaluation \
  --project-json runtime/profile-examples/compact6/project.json \
  --root runtime/profile-examples/compact6 \
  --output runtime/evaluations/compact6-run-001
```

默认不调用ASR/VLM或视频生成服务，只使用已有文件。输出目录必须不存在，且不能位于输入素材的production内。缺少字幕、交付文件、镜头或校验失败时返回2；返回0只说明报告流程完成，必须另看verdict，不能把human_review_required当成画面质量通过。

部署中的数据库可明确以只读方式打开，不调用store.initialize、不修改项目、不领取任务或提交生成：

```bash
python -m factory.evaluation --project-id PROJECT_ID \
  --database /path/to/existing/studio.sqlite3 --root /path/to/checkout \
  --output /path/to/new-evaluation-directory
```

实际交付约定为episode.mp4、episode_clean.mp4、subtitles.zh.srt、timeline.json、audio_edit.json。文件必须与记录的SHA-256一致；视频完整解码，检查帧率、时长、尺寸、音频。字幕核对当前对白、时间顺序、成片边界和所属镜头槽位；时间线核对实际镜头顺序和逐镜时长。字体可读性、遮挡、安全区及真实视听效果仍需人工审查。

## 输出与样片验收报告模板

| 文件 | 内容 |
| --- | --- |
| report.json | 完整检查结果、项目/镜头/模型/费用观测、协议和代码签名、媒体校验值、返修建议 |
| metrics.csv | 逐行category/check/shot_id/source/status，文本经过公式注入防护 |
| human-review-template.json | 已绑定project_id、production_revision、media_signature的待填写人工审片单 |
| export-manifest.json | 所有报告及可选调用回执的大小/SHA-256 |
| observations/ | 仅显式VLM评估时保留本次调用回执，不写回原项目 |

样片验收填写：作品/镜头版本及校验值、模型配置、耗时/重试/费用、七个维度的具体时码证据、未覆盖范围、审片人、日期和批准/返修决定。模板所有维度均为pending，不预填通过。填写reviewer、reviewed_at、decision=approve或revise，以及全部dimensions的status=pass/fail/uncertain和非空evidence后，显式重新运行：

```bash
python -m factory.evaluation --project-json project.json --root /input/root \
  --human-review /review/filled-human-review.json --output /reports/new-human-reviewed-run
```

不同项目、过期生产版本或媒体校验不匹配的审片单拒绝。human_approved表示导入了绑定当前媒体的人工结论，不是自动艺术质量认证；artistic_quality_certified始终为false。本地审片记录依赖可信操作者，第一阶段不是多用户鉴权/电子签章系统。

## ASR/CER 自动化接入

复用audio/check_dialogue.py的NFKC、繁简归一化、逐字CER和带声调拼音误差。新compare_transcript/check_lines与原CLI共用实现。按对白ID查找assets.audio.lines，核对当前文字、角色和WAV校验，不能把旧台词识别结果套到新对白。

CPU CI只安装OpenCC和pypinyin，不安装/下载ASR模型。真实ASR需另行准备faster-whisper运行环境和含model.bin、download_receipt.json的已有模型目录：

```bash
python -m factory.evaluation --project-json project.json --root /input/root \
  --asr-model /already-installed/whisper-model --output /reports/asr-run
```

模型以local_files_only=True加载；中文盲识别不提供目标句子/hotwords，报告分段时间、CER、拼音误差和模型来源。exact_match是归一化匹配；homophone_match仍需复听，不能证明文字正确。专名、口音、音乐、低音量和同音字可能误判；自然度、情绪、表演与可理解性不能由CER替代。本轮没有运行真实ASR模型。

## 显式 VLM 与时序审片

```bash
python -m factory.evaluation --project-json project.json --root /input/root \
  --vlm-config /path/to/evaluation-config.json --confirm-vlm-requests \
  --output /reports/vlm-run
```

必须同时提供配置和确认标志才请求已有visual_critic连接。此为评估推理而非视频生成POST，但可能产生所配置模型的费用，因此不自动开启。视频新生成仍须通过优化1的授权账本，本模块不生成/重发视频任务。

quality.temporal_samples允许4–16整数，默认8。抽帧策略进入质检签名，修改抽样重审而不无条件重生成视频。跨镜配对覆盖相邻镜头和非相邻同人物再次出场；参考图身份用途与实际剧情证据分离，评审只能引用程序产生的帧号/时间目录，未知证据ID拒绝。uncertain、协议错误、网络失败与pass不同。本轮VLM只使用mock验证证据约束，不把模拟分数当成实际主观质量。

## 可选 VBench 离线指标

第一阶段实现已有离线结果导入，不自动安装VBench/下载权重。操作者可在固定上游版本、预装模型、网络禁用的独立环境运行VBench；可用维度、依赖和分数定义以该固定版本为准，不在CPU CI执行。保留原始结果，再转换为带证据的导入格式：

```json
{"tool":"VBench","tool_revision":"实际固定的上游提交SHA","protocol":"实际维度、参数和数据范围","weight_provenance":"预装权重的来源和校验记录","results":[{"shot_id":"S01","media_sha256":"实际视频的64位SHA256","scores":{"subject_consistency":0.0}}]}
```

0.0仅为格式占位，不是样片实测分，需替换为真实结果后使用：

```bash
python -m factory.evaluation --project-json project.json --root /input/root \
  --vbench-report /metrics/verified-vbench-export.json --output /reports/vbench-import-run
```

验证媒体校验值和有限数值，保留原始分数及协议，不设置艺术质量通过阈值。未跑明确not_run；导入分数为review_needed而不是人工批准。本轮没有VBench实测成绩。

## 问题分类和返修

| 类别 | 验收依据 | 优先返修范围 |
| --- | --- | --- |
| causality 剧情因果 | 可见前因、选择、结果及信息来源 | 相关剧情镜头，不以摘要替代画面 |
| identity 角色 | 固定身份对比参考及跨镜样本 | 对应人物参考/关键帧；服装状态单独审查 |
| props 道具 | 件数、持有人、交接及剧情状态 | 出错镜头与必要交接，保留无关缓存 |
| dialogue 对白 | 实测占用、盲ASR、人工复听 | 配音、台词或混音，不默认重生成画面 |
| action 动作 | 多时间点的单一动作证据 | 简化动作或局部重制，相机运动不冒充表演 |
| subtitles 字幕 | 文字/时间检查及人工可读性 | 字幕与重合成，不默认重拍 |
| delivery 交付 | 校验、解码、完整镜头及时间线 | 恢复下载/补导出/编码，不重投未知云任务 |

## 回归与真实浏览器

```bash
python -m pytest -q tests/test_factory_*.py
npm ci --ignore-scripts
node --test tests/test_factory*.mjs
python scripts/browser_fixtures.py
npx playwright install --with-deps chromium
npm run test:browser
```

下载测试浏览器不等于下载AI模型。Playwright启动独立127.0.0.1模拟服务，不接触生产数据库，也不代理供应商。测试覆盖项目列表、模型编辑保存/预检、实际视频预览、出品人决策、播放时更新其他区域、失败提示和CAS旧草稿。断言同一个video节点保持connected、currentTime增长、没有emptied事件，不以DOM字符串测试代替浏览器播放验证。

本次还在官方Playwright容器中用--network=none运行全部冒烟测试，仅挂载隔离worktree，无GPU设备或生产目录挂载。截图、results.json和失败trace保留在runtime/browser-report。CI继续CPU模拟测试；真实GPU/云样片需操作者显式生成并审批，再运行上述只读评估命令，本轮没有真实生成。

比较报告加 `--baseline /old/report.json`。golden协议和production_profile签名必须一致才比较，输出状态变化，不把不同协议/片长/人物的差异当成模型进步。

## 参考、迁移与实际修改

方法参考：[VBench](https://github.com/Vchitect/VBench) 的维度拆分；[Langfuse data model](https://langfuse.com/docs/observability/data-model) 的运行观测关联；[Playwright Docker](https://playwright.dev/docs/docker) 的独立浏览器运行。未部署Langfuse，未声称模拟样片通过VBench或人工艺术验收。

新增evaluation.py、evaluation_io.py、evaluation_media.py、evaluation_external.py、evaluation_sampling.py、evaluation_semantic.py、golden.json、评估测试、浏览器服务/测试、browser_fixtures.py和Playwright配置。既有llm/config/production适配时序质检签名，audio/check_dialogue复用CER，前端保留失败时的旧快照并禁用编辑，CI安装小型评估依赖。没有新增状态数据库；仅输出报告文件，不迁移或修改运行中生产数据。


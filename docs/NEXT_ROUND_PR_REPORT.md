# 下一轮 PR 交付报告

开发基线：`origin/main@4aaece74ab54f38c125c564accc20f4134fb5bab`，恢复后再次 fetch 确认。只在隔离 worktree 开发，不更新生产配置、数据库、服务或模型环境。

## 优化 7：已交付的独立分支

[Draft PR #8](https://github.com/Xiiiing/ai-studio/pull/8)，head=`feat/http-security-hardening`，base=`main`，最终核查 head SHA `41bdb8ce4f37a92936a6b971d3d454f89dcf3806`。[CI run 38496985083](https://github.com/Xiiiing/ai-studio/actions/runs/38496985083) completed/success。状态 implemented、tested、pushed、draft_pr_open、ci_passed；未合并。此 PR 不含本分支的验收业务代码。

## 优化 8：通用只读验收

独立分支 `test/production-acceptance-v2`，base=`main`，不依赖 PR #8。状态：implemented、tested（Python、Node、真实浏览器）；远端交付核验尚待更新。远端 PR URL 不预填，pushed、draft_pr_open、ci_passed 在实际完成前为 not_run。

### 实现与源码依据

旧专项检查单独放入 `factory.acceptance_legacy` 并要求显式副作用授权；新的 `factory.acceptance` 复用原 film-evaluation v1 与七维规则。模块分为 inputs、checks、audio、baseline、reports；不引入第二套评分或任务系统。SQLite mode=ro 的同一事务读取项目/任务；项目 ID、production revision、media SHA 明确绑定；技术、未运行模型和人工证据分离；独立目录与最后写入的完成清单支持中断诊断和可校验基线。

上游固定版本：drama-skills `c2426e03c0e7722bebcc6a488b6658dc38c65ac3`（MIT），研究 evaluations/README.md、content_quality_gate.py 和 tests/test_content_quality_gate.py；VBench `fd18b3d055cb0fc6f066ca90fe2c3c8cbb698490`（Apache-2.0），研究 vbench/__init__.py 和 subject_consistency.py。许可、设计适配和未采用机制见 [PRODUCTION_ACCEPTANCE.md](PRODUCTION_ACCEPTANCE.md)。未复制第三方源码、语料或模型。

### 实际失败前证据与修复

1. 最初 8 项新契约测试全失败：通用只读接口不存在，跨项目/代码版本比较不拒绝，移除检查不报告。
2. CPU 媒体用例初次 17 passed / 2 failed：现有长句拆分字幕被旧单条假设误拒，过期人工证据应形成失败报告而非中断无报告。
3. 14 项证据补充测试修复前全失败：模糊 JSON、完整性清单、媒体/锁定剧情字段和时长元数据等。
4. Runner 上次失联导致编辑 outcome_unknown。恢复后已只读确认两项补充测试及文档修正确实落盘，没有盲目重放。
5. 本次继续审查新增 8 项，修复前全失败：真实原混音器不输出 audio_edit 总时长；净版解码后变化未二次校验；5 类非法生产版本参与报告路径；完成清单可漏掉失败证据。修复不改原混音器、不放宽实际媒体时长，验证的是原生产格式。

最后一轮完整 Python 复测：专项 88 passed，45.12 s；完整 tests/test_factory_*.py 为 550 passed，124.96 s，5 条既有多线程 fork DeprecationWarning 保留。对应日志 runtime/audit-evidence/recovery-focused.log 与 recovery-python-all.log，已核对日志存在且运行结果一致。早期 540 项通过不是最终新增测试的替代证据。

前端同一固定 Playwright 镜像复测：`npm ci --ignore-scripts --offline` 成功；`node --test tests/test_factory*.mjs` 为 54 passed、0 skipped；`python scripts/browser_fixtures.py` 成功；`npm run test:browser` 为 5 passed、15.9 s、0 skipped。日志 `runtime/audit-evidence/recovery-browser-final.log`。浏览器分支不包含优化 7 的 3 项 HTTP 增量用例，没有删除既有测试。环境 Node 24.21.0、Python 3.12.13；镜像 `mcr.microsoft.com/playwright@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f`，network=none、2 CPU、4 GiB、有界进程数，无 GPU、生产数据或 Docker socket 挂载。合成媒体 SHA 与原 browser fixture 一致。

测试后核对所有 factory/ 与 tests/ 改动仍与已导出的恢复包逐字节一致：恢复包 SHA-256 `4432b6c6d6bb1439f2e6f6e28365589d2e04bc56ba817f6910e7835cc6d630c8`。该包仅用于保留当前改动，不是工作包 9 的生产一致性备份。

测试包括三种现有规格真实 CPU FFmpeg/PyAV 解码、原混音器输出、分段字幕、人工旧证据拒绝、两个项目独立目录、WAL 并发读取、缺成片/媒体/字幕、SHA/版本不匹配、导出中断、错误基线、生产文件校验不变。CPU 测试图样/正弦音频及模拟人工记录均不是模型生成或实际艺术审片。

### 安全与兼容性

无数据库 Schema 迁移，无生产读写实验、服务重启或收费请求。旧项目不被自动补写字段；缺失证据保持失败/unknown/not_run。历史专项报告可读但不伪造可比性；新报告仍沿用原评估外层格式。media_context 仅在通用验收中加强人工证据绑定，原 evaluation 默认接口保留。

完整性清单不是密码学身份认证；项目与文件前后复查不构成原子多数据库快照。复杂历史 reedit 的 source_range 等额外能力不在本 PR 声称覆盖范围内。原样片专项副作用路径、真实 GPU/TTS/LLM/云视频、艺术质量和外部代理部署均 not_run。

### 部署、回滚与后续

部署由操作者自行安排，本轮不执行。新 CLI 只向非 production 独立目录追加报告；回退代码无需数据库逆迁移，新报告可以保留但不能被旧版本冒认为同协议通过。多个独立 PR 的同名 NEXT_ROUND 文档可能在人工集成时产生 add/add 冲突，须合并审计记录，不能因此错误串联独立业务分支。

优化 9–15 及整轮组合回归未执行，状态 not_run。GitHub CI 必须绑定实际 head SHA 单独记录，不把本地通过或前一 PR 的 CI 当作本分支通过。

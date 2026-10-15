# 下一轮 PR 交付报告：优化 10

## 已发布的正式 PR

[PR #11：完整版本化创作 Prompt、离线盲评协议与证据边界修复](https://github.com/Xiiiing/ai-studio/pull/11)

- 分支：`feat/versioned-creative-prompts`；base：`main`。
- 审查基线：`4aaece74ab54f38c125c564accc20f4134fb5bab`。
- 原实现：`89c7495b06e36a3df37dba73119ef02f84c3e51c`。
- 已测试修复版代码：`4244f7285e1170ca333bd03ac80ca7bf23a47d42`。
- PR 实际创建状态：open，draft=false，未合并。随后报告提交只增加/更新文档。

状态：implemented、tested、pushed、pr_open；draft_pr_open 不适用，用户要求非草稿。
CI 运行 [38673954572](https://github.com/Xiiiing/ai-studio/actions/runs/38673954572)
对应上述代码提交；本报告写入时为 pending，不借用其他提交或 PR 的成功结果。
后续文档提交触发的新 CI 应按其实际 head 单独核查，最新证据记录在 PR 检查页与评论中。

## 文件范围与实际验证

原业务接入仅改 creative/storyboard/profile_creative/schemas/llm，保留原缓存内容键。
新增 prompt_registry、creative_experiment、creative_comparison、creative_evidence_io、
creative_experiment_cli、creative_holdout；catalog 加五份 v1 原文、完整六案例语料、
禁用模型示例、六个专项测试文件及原文 SHA 夹具。文档包含三份 NEXT_ROUND 报告。
不包含 runtime、生产媒体、数据库、私人配置、密钥、模型权重、依赖锁或其他优化代码。

| 本次实际执行 | 结果 |
| --- | --- |
| 新增发布回归修复前 | 7 failed，0 passed，保留反例日志 |
| 六个 Prompt/A-B 专项测试文件 | 61 passed，9.54s |
| Python3.12.13：python -m pytest -q tests/test_factory_*.py | 555 passed，84.38s；1 条原 multiprocessing/fork 警告保留 |
| npm ci --ignore-scripts | 成功，锁文件未变 |
| node --test tests/test_factory*.mjs | 54 passed，0 failed，0 skipped |
| python scripts/browser_fixtures.py | CPU 测试媒体成功 |
| npm run test:browser | 5 passed，13.9s；真实 Chromium + Mock API |

日志位于原隔离 worktree 的 `runtime/release-evidence/`。上次 548/54/5 的记录另存于
`runtime/audit-evidence/`，不冒充本次执行。生产模型、GPU、云视频及艺术质量均 not_run。

## 发布恢复和可追溯性

全部测试完成后，远程 Runner 断线，文档编辑返回 not_started，未派发也未修改文件。
未停止或重启任何服务。原工作区保持保留，不能声称断线后已在原工作区提交或清理。
通过已授权 GitHub 连接补齐遗漏 corpus，其 blob 为 `86d8920bfc244bc9facc9e7f69dc777c6f984d9f`，
原完整 tree 与本地原提交相同：`ed71687fd3660061fc9396ccf9d286b2ff90b332`。
修复 commit 的回读 diff 与刚通过测试的 7 文件变更一致；不是从记忆重写全部工程。
文档在独立目录整理检查后发布。未使用强推，不向 main 提交，不自动合并。

已有独立工作包入口：[优化7 #8](https://github.com/Xiiiing/ai-studio/pull/8)、
[优化8 #9](https://github.com/Xiiiing/ai-studio/pull/9)、
[优化9 #10](https://github.com/Xiiiing/ai-studio/pull/10)。本次不修改这些 PR。

## 部署与回滚

部署代码时一并携带 `factory/prompts/`。无需数据库迁移；本轮不部署、不改生产配置。
回退代码不重写旧缓存/媒体。已冻结实验仍需原精确源码与可信 seal/ledger；
新代码不静默接受旧源码封印，不删除中断预留以重新获得 holdout 调参机会。

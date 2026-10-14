# 下一轮架构审查记录

最新获取的开发基线：`origin/main@4aaece74ab54f38c125c564accc20f4134fb5bab`。本文件属于优化 8 的独立分支，未合并其他工作包代码。

## 已确认的问题

优化 7 的 HTTP 入口问题已在 [Draft PR #8](https://github.com/Xiiiing/ai-studio/pull/8) 单独修复；该 PR 的最终 head `41bdb8ce4f37a92936a6b971d3d454f89dcf3806` 对应 CI run `38496985083` 已 success。它尚未合并，不是本分支 base。

优化 8 的原 `factory.acceptance` 实际包含固定历史 manifest SHA、固定 systemd 名称、全局固定路径写入和项目报告覆盖，不适合作为通用只读验收。既有 `factory.evaluation` 已提供可复用的七维评估和只读 DB 入口，因此在其上增加契约，而非重做评分器。

隔离测试进一步确认：旧回归比较没有严格阻止跨项目/不同评价代码比较，也漏报被移除的检查；字幕评估会拒绝现有剪辑器实际产生的长句拆分字幕；JSON 证据解析允许重复成员和溢出数值。所有问题的修复前失败、适配与限制见 [PRODUCTION_ACCEPTANCE.md](PRODUCTION_ACCEPTANCE.md) 和 [PR 报告](NEXT_ROUND_PR_REPORT.md)。没有执行生产攻击、修复生产数据或调用真实模型。

## 工作包状态

| 优化 | 独立分支 | 状态 | base / 依赖 |
| --- | --- | --- | --- |
| 7 HTTP 安全 | feat/http-security-hardening | implemented、tested、pushed、draft_pr_open、ci_passed；[PR #8](https://github.com/Xiiiing/ai-studio/pull/8) | main |
| 8 通用验收 | test/production-acceptance-v2 | implemented；本地最终复测与远端交付状态见 PR 报告 | main，不依赖 7 |
| 9 一致备份 | feat/consistent-project-backup | not_run | main |
| 10 创作 Prompt | feat/versioned-creative-prompts | not_run | main |
| 11 生产追踪 | feat/production-tracing | not_run | main |
| 12 多轨时间线 | feat/versioned-media-timeline | not_run | main |
| 13 持久化/GPU profiling | perf/persistence-and-gpu-profiling | not_run | 11；未合并时 stacked base |
| 14 工作台增量同步 | perf/workbench-incremental-sync | not_run | main |
| 15 素材生命周期 | feat/artifact-retention-manager | not_run | 9；未合并时 stacked base |

未实施工作包中的用户所述问题尚未逐项实证，不能仅因写在需求中就列为已确认漏洞。独立分支中的这些共享审查文档可能在人工集成时产生 add/add 冲突，需合并审计记录；不应为避免文档冲突而让业务分支错误地依赖所有未合并 PR。

# 下一轮架构审查记录

审查仓库：Xiiiing/ai-studio。首次及工作包 7 开发基线为最新获取的 `origin/main@4aaece74ab54f38c125c564accc20f4134fb5bab`，不以源工作目录的旧 HEAD 作为开发基线。

## 已验证的发现

工作包 7 的实际代码、完整路由清单、触发条件、临时 HTTP/SQLite 测试和固定上游源码/许可记录见 [HTTP_SECURITY.md](HTTP_SECURITY.md)。Factory 继承 Legacy 的部分路径缺少统一准入得到隔离测试证据；没有把这描述为已在生产复现的攻击。现有 providers/video_transport 网络约束、费用账本、未知云 POST 恢复语义没有重复实现或放宽。

## 工作包状态

| 优化 | 独立分支 | 当前审查 / 实现状态 | 依赖 |
| --- | --- | --- | --- |
| 7 HTTP 安全 | feat/http-security-hardening | implemented、tested、pushed、draft_pr_open：[PR #8](https://github.com/Xiiiing/ai-studio/pull/8)；CI pending | main |
| 8 通用验收 | test/production-acceptance-v2 | not_run；用户列举的问题仍需逐项以源码确认 | main |
| 9 一致备份 | feat/consistent-project-backup | not_run | main |
| 10 创作 Prompt | feat/versioned-creative-prompts | not_run | main |
| 11 生产追踪 | feat/production-tracing | not_run | main |
| 12 媒体时间线 | feat/versioned-media-timeline | not_run | main |
| 13 持久化/GPU profiling | perf/persistence-and-gpu-profiling | not_run | 11；未合并时应使用 stacked base |
| 14 工作台增量同步 | perf/workbench-incremental-sync | not_run | main |
| 15 素材生命周期 | feat/artifact-retention-manager | not_run | 9；未合并时应使用 stacked base |

`not_run` 不是通过，也不是对用户提供的风险描述作了事实确认。每项完成后应记录其独立审查与测试证据，不能将不同工作包业务代码放入同一 feature 分支。

## 全局安全边界

本轮只在独立 Git worktree、临时数据库和合成媒体上测试。不修改源工作目录、运行中的生产数据库/配置/服务，不触发付费模型或自动合并。GPU、真实云视频与艺术质量验证均为 not_run；Mock 成功不构成这些能力通过的证据。

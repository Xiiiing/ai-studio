# 下一轮审查：优化9独立分支

实际开发基线 `origin/main@4aaece74ab54f38c125c564accc20f4134fb5bab`。本分支 `feat/consistent-project-backup` 直接基于 main，不包含未合并优化7或8的业务代码。

## 已证实的结构问题

当前 store 数据库同时保存项目、队列、费用和系列依赖；workflows.run 用项目ID作为SqliteSaver thread_id，检查点位于另一数据库；系列资产在production/series-assets，生成回执和历史素材位于各项目目录。只复制一个studio.sqlite3不能保留这些实际依赖。本次没有对真实生产实例做复制/恢复实验。

新备份协议通过明确维护窗口、双SQLite写入锁、Backup API、全量生产文件清单、源/目标回读和全数据库逻辑摘要建立可核验边界，不宣称文件系统原子快照。默认恢复新目录，排除配置/凭据收集，不执行工作流或云请求。完整限制、源码依据和许可见 [CONSISTENT_BACKUP.md](CONSISTENT_BACKUP.md)。

| 优化 | 分支 | 状态 / 已知远端 | base |
| --- | --- | --- | --- |
| 7 HTTP | feat/http-security-hardening | [Draft PR #8](https://github.com/Xiiiing/ai-studio/pull/8)，implemented/tested/pushed/draft_pr_open/ci_passed | main |
| 8 验收 | test/production-acceptance-v2 | [Draft PR #9](https://github.com/Xiiiing/ai-studio/pull/9)，最终head893df2410e832288313bf4306ff69f6af3c17770、CI38507037044 success | main |
| 9 备份 | feat/consistent-project-backup | implemented；最终测试/交付状态见本分支PR报告，未预填URL | main |
| 10 Prompt | feat/versioned-creative-prompts | not_run | main |
| 11 Trace | feat/production-tracing | not_run | main |
| 12 Timeline | feat/versioned-media-timeline | not_run | main |
| 13 Persistence/GPU | perf/persistence-and-gpu-profiling | not_run | 11 |
| 14 Workbench | perf/workbench-incremental-sync | not_run | main |
| 15 Retention | feat/artifact-retention-manager | not_run | 9，需验证恢复后才允许实际回收 |

工作包15尚未开始，当前备份实现不是对自动回收流程的授权。各独立分支共享NEXT_ROUND文档可能产生add/add冲突；人工集成要保留各包实际证据，不应为文档方便错误串联PR依赖。

# 下一轮架构审查记录

仓库 Xiiiing/ai-studio；最新获取及开发基线 `origin/main@4aaece74ab54f38c125c564accc20f4134fb5bab`。工作包 7 已完成源码审查、独立实现及离线测试，详细证据和固定上游源码/许可见 [HTTP_SECURITY.md](HTTP_SECURITY.md)。没有重复实现原 PR #2–#7 的能力。

| 优化 | 独立分支 | 当前状态 | 前置 |
| --- | --- | --- | --- |
| 7 HTTP 边界 | feat/http-security-hardening | implemented；Python/Node tested；本地 browser blocked | main |
| 8 通用验收 | test/production-acceptance-v2 | preliminary audit；implementation not_run | main |
| 9 一致备份 | feat/consistent-project-backup | not_run | main |
| 10 Prompt 版本 | feat/versioned-creative-prompts | not_run | main |
| 11 生产追踪 | feat/production-tracing | not_run | main |
| 12 媒体时间线 | feat/versioned-media-timeline | not_run | main |
| 13 持久化/GPU profiling | perf/persistence-and-gpu-profiling | not_run | 11，未合并则 stacked |
| 14 工作台增量同步 | perf/workbench-incremental-sync | not_run | main |
| 15 素材生命周期 | feat/artifact-retention-manager | not_run | 9，未合并则 stacked |

工作包 8 初读确认 acceptance.py 包含固定 the-last-stop manifest SHA、固定 systemd 名称和两处会覆盖的报告输出；evaluation.py/evaluation_io.py 已有可复用的只读评估与独立输出框架。尚未完成该包设计、上游源码学习或代码，不能把用户列举的其他风险一概记为已证实。

全程只使用隔离工作树、临时 SQLite 和 CPU 合成媒体。原开发连接失去命令响应后，保留其工作树和未确认文档写入，不重试未知写操作；在新独立目录恢复已校验 SHA 的源码导出。没有修改生产数据/配置/服务、执行收费调用或自动合并。其他工作包必须各自基于 main 或正确 stacked base 实施。

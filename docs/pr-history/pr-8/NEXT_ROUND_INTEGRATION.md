# 下一轮集成回归记录

## 当前验证范围

已核查 [Draft PR #8](https://github.com/Xiiiing/ai-studio/pull/8) 的 base=main、head=feat/http-security-hardening，未合并。本轮实现提交 `bd6da8ab9c2be2b115bf95ecdf03b1c2cfef7542` 的 tree 与本地通过回归的版本完全一致；GitHub CI 记录时 pending，不能替代以下本地证据。

工作包 7 基于 `main@4aaece74ab54f38c125c564accc20f4134fb5bab`，没有合并其他工作包的实现。运行完整 Factory Python 回归和 Node 单元测试，新增真实 loopback HTTP/临时 SQLite 契约测试。它们验证离线控制逻辑，不构成全流程真实制作验收。

| 不变量 | 当前证据 / 状态 |
| --- | --- |
| 未知云 POST 不重复收费提交 | 原远程任务和费用测试包含在 663 项回归内；生产供应商调用 not_run |
| 剪辑变更不重新生成视频、局部修改不影响无关镜头 | 保留原实现与回归；新增时间线功能 not_run |
| 已批准素材与成片不被覆盖 | HTTP 改动只改变访问和任务控制；生产文件未修改 |
| 不用当前配置伪造历史模型身份 | 原模型身份/回执测试保留；新追踪与 Prompt 版本功能 not_run |
| 系列参考不静默升级、人工审片不被分数替代 | 原系列/人工决策回归保留，无业务改写 |
| 暂停保留 task_id、file_id、未知状态与账本 | 新 job API 临时 SQLite 测试直接比较前后账本 |
| 过期状态不能控制新执行 | 新控制的 expected_revision + expected_lease；原选片/配置 CAS 保留 |
| 浏览器行为不由 Mock 代替真实边界验证 | 真实 Python HTTP 的 3 项浏览器用例与原工作台 5 项用例均通过；CPU 合成媒体，模型质量仍 not_run |
| 备份恢复保留收费任务 | 备份恢复工作包 not_run，未做生产恢复实验 |
| 模拟测试不冒称生产质量 | 模型/GPU/真实云/艺术质量全部明确 not_run |

## 尚未完成的集成门禁

创建项目 → 创作 → 分镜 → 系列资产 → 授权 → 图像/视频 → 恢复 → 选片 → 返修 → 多轨剪辑 → 通用验收 → 备份恢复的**整链路组合验证尚未运行**。不得把单分支单元测试通过记为该门禁通过。后续集成记录需固定实际组合的各 PR head SHA，在新的隔离测试目录运行，不能触碰生产服务或自动合并 PR。

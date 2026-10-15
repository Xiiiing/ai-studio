# 下一轮集成回归：优化 10

## 本 PR 的实际回归范围

基于 main `4aaece74ab54f38c125c564accc20f4134fb5bab` 加完整优化 10 和发布前修复，
本次实际运行全部 `tests/test_factory_*.py`：555 passed，84.38s；1 条原 fork 警告保留。
Node 54 passed；Chromium 浏览器 5 passed，13.9s。没有删减或跳过原测试。
对应发布代码 commit：`4244f7285e1170ca333bd03ac80ca7bf23a47d42`。
新增专项共61项；五份主要 Prompt 和完整六案例语料与原实现保持不变。

浏览器采用预先存在的固定镜像：
`mcr.microsoft.com/playwright@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f`。
容器 network=none、2CPU、4GiB、pids-limit=256，无 GPU/生产数据库/Docker socket/端口发布。
媒体是 CPU 测试图案，API 是 Mock，不能称为真实成片或生成链质量验收。

## 不变量与证据边界

| 不变量 | 覆盖 |
| --- | --- |
| 未知云 POST 不重复收费 | 原 remote_tasks 崩溃恢复回归通过；本 PR 不改提交/预算路径 |
| 剪辑变更不重新生成视频 | 原制作/缓存回归通过；本 PR 未实现新时间线 |
| 局部修改不解锁无关镜头 | 原导演、局部修订、选片回归通过；Prompt 沿用原内容签名 |
| 批准媒体不覆盖 | 未操作生产路径；原工作台/制作回归通过 |
| 不补造历史模型/Prompt 身份 | 原模型路由与新增 unknown/version 测试通过 |
| 系列参考不静默升级 | 原 Series 回归通过，本 PR 不改绑定模块 |
| 自动评分不代替人工审片 | 实验 production_approval 始终 false；没有真实艺术质量结论 |
| 恢复保留收费信息 | 原远程任务回归通过；与备份 PR 最新组合另列 not_run |
| 旧前端不覆盖新版本 | 原 CAS 和浏览器旧草稿回归通过 |
| Mock 不声称生产质量 | 模拟结果保持 not_run/not_established，缺项不产生汇总成绩 |

## 跨 PR 和真实环境

本次未运行优化 7–10 最新组合的 detached 集成回归：not_run。
远程 Runner 在单分支测试完成后断线；不能把各分支通过数量相加代替组合测试。
优化 7–9 保留各自独立 PR，不错误串联为本 PR 的 base；没有把组合代码混入本分支。

真实 GPU、TTS/ASR/LLM、云视频、收费审批后的实机制作、生产备份恢复、部署以及
优化 11–15 均 not_run。已完成的是本功能包软件回归和离线协议验证，不是整个7–15链路验收。
后续组合验证必须记录真实来源 SHA、组合 tree 和实际命令结果。发布检查见
[NEXT_ROUND_PR_REPORT.md](NEXT_ROUND_PR_REPORT.md) 与 [PR #11](https://github.com/Xiiiing/ai-studio/pull/11)。

# 下一轮 PR 交付报告：优化9

基线：实际获取的 `origin/main@4aaece74ab54f38c125c564accc20f4134fb5bab`。本分支为 `feat/consistent-project-backup`，base=main，与优化7/8独立。

## 已有独立交付

优化7：[Draft PR #8](https://github.com/Xiiiing/ai-studio/pull/8)，head41bdb8ce4f37a92936a6b971d3d454f89dcf3806，CI38496985083 success。优化8：[Draft PR #9](https://github.com/Xiiiing/ai-studio/pull/9)，最终head893df2410e832288313bf4306ff69f6af3c17770，CI38507037044 success。两者均未合并。本分支没有复制其业务实现。

## 优化9本地状态

implemented、tested。pushed、draft_pr_open、ci_passed 在真实远端核查前为 not_run，不预填PR URL。源码为5个独立backup模块，2个测试文件和4份文档；没有修改原store/workflows/provider/worker/前端/配置/依赖，也没有改变原数据库模式。

实现：明确操作者维护窗口确认、既有worker锁检测、双SQLite BEGIN IMMEDIATE写入保留锁、独立只读源连接Backup API、全实例媒体/系列引用闭合、源和目标文件哈希回读、数据库完整逻辑行/BLOB摘要、同一快照ID配对、完成清单最后写入。restore拒绝已有或源内目录，保存原数据及未确定云任务，不反序列化检查点、不调用recover/claim/网络，不自动运行制作。

参考和实际源码：restic/restic `9da3b87b07706956e8dc5fd5cb259b99ec940f51` 的 internal/data/snapshot.go、internal/restorer/restorer.go 与 restorer_test.go（BSD-2-Clause）；langchain-ai/langgraph `a6731f436700c7d46a9224ccd3a0e0ce3a5bfdb5` 的 SqliteSaver、test_sqlite.py、pregel/_retry.py（MIT）；SQLite官方Backup API与Python3.12文档。已读许可证，未复制上游源码/测试或加入新存储平台，具体取舍见 [CONSISTENT_BACKUP.md](CONSISTENT_BACKUP.md)。

## 实际测试与失败证据

| 命令 / 环境 | 实际结果 |
| --- | --- |
| 初始 tests/test_factory_backup.py | 18 failed：主分支没有所需备份/恢复接口；不是生产故障或漏洞利用 |
| 新增 sidecar/边界元数据反例 | 修复前失败，日志 backup-safety-before.log；随后严格拒绝不完整或额外WAL状态 |
| 未引用历史文件目标损坏 | backup与restore两项修复前失败；补全部目标回读，不仅核对当前引用 |
| 任意费用字段副本变化 | 修复前失败；补锁内源与副本全逻辑内容摘要，不新增第二账本 |
| Python3.12.13：python -m pytest -q tests/test_factory_backup*.py | 36 passed，2.74 s |
| 宿主完整 tests/test_factory_*.py | 529 passed / 1 failed，94.05 s；原DNS超时断言实际约3.92s超过0.25s；1条原fork警告保留 |
| 宿主 providers 单独复测 | 67 passed / 1 failed，同一原DNS断言约1.18s；未改源码或阈值 |
| 固定隔离容器 providers 单独复测 | 68 passed，4.70 s，原测试与阈值 |
| 同容器 python -m pytest -q tests/test_factory_*.py | 530 passed，160.47 s，原测试无跳过 |
| npm ci --ignore-scripts --offline | 成功，固定lock和离线缓存 |
| node --test tests/test_factory*.mjs | 54 passed、0 skipped |
| python scripts/browser_fixtures.py | CPU合成媒体成功，未运行生成模型 |
| npm run test:browser | 5 passed、20.2 s、0 skipped，原配置/超时/断言 |

宿主DNS延迟与容器结果不同，根因尚未确定，不将一次容器通过当作已修复宿主DNS性能问题。providers.py和原测试没有差异；相关日志均保留，不通过放宽或删除测试掩盖失败。

最终Python日志 runtime/audit-evidence/backup-focused-final.log、backup-python-container-final.log；宿主失败 backup-python-final.log、backup-dns-recheck.log；浏览器 backup-browser.log；修复前反例 backup-copy-before.log、backup-ledger-before.log。容器固定镜像 mcr.microsoft.com/playwright@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f，network=none、2CPU、4GiB物理内存限制、无GPU/生产DB/socket/端口发布。宿主内核不支持swap limit，运行时警告原样保留，不声称swap也已限额。

测试只使用临时SQLite/WAL、合成/不透明媒体和测试自建真实LangGraph人工中断图。恢复后只读取该测试图的状态/interrupt，没有继续调用节点。包括未完成云任务、pending writes、系列共享版本、外置DATA路径变化、符号链接/非法路径、缺失/哈希不匹配/版本错配、活动worker和DB写者、已有目录/重复恢复、复制中断及后验审计。

## 安全、兼容与未验证

无生产读写/恢复实验、无模型/GPU/云调用、无服务停止/重启、无真实凭据提交。源库不迁移，副本切为自包含DELETE journal；原任务状态和账本原样保存。文件/数据库前后比较不等于文件系统原子快照；维护窗口是真实前提，不能只传布尔值就证明其他写者已经停止。

支持当前已审查的Schema、共同快照协议和新目录恢复；不支持无清单旧文件复制、未知备份版本、任意未来表模式或单项目跨共享DB拆分。历史业务BLOB是原样备份，不承诺自动清除错误内嵌其中的秘密；私配和环境值默认不采集，备份仍应受控存储。恢复仅为数据目录，投入实际运行前须人工配置兼容环境并核对未知付费任务，不可让源/恢复实例同时制作。

GPU、云端、真实模型/艺术质量、生产数据恢复、跨主机部署与完整7–15链路均not_run。优化10–15未实施。后续真实回收必须另经授权并以完整性/恢复结果为前提，本工具不执行任何永久删除。

## 部署与回滚

由操作者自行安排维护窗口和部署，本轮不执行。CLI只向外部新目录生成快照或恢复数据；回退工具代码不改源数据，无逆向数据库迁移。所有半成品目录保留，不能用覆盖重试掩盖中断。共享NEXT_ROUND文档的add/add冲突需要人工合并实际审计记录，不应错误串联独立PR。

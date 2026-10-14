# 下一轮 PR 交付报告

基线 `4aaece74ab54f38c125c564accc20f4134fb5bab`。取得真实远端结果后才记录 URL，不预填 PR 编号。

## 工作包 7

分支 `feat/http-security-hardening`，base `main`。状态 implemented，Python/Node tested；pushed、draft_pr_open、ci_passed 当前 not_run。本地 browser 为 blocked。

文件范围：studio/http_routes.py、http_security.py、http_media.py、web.py；factory/web.py、job_api.py、store.py；对应 Python/浏览器测试、临时浏览器 fixture、部署说明和本轮审查文档。未改云提交/账本/GPU Lease/Schema。

| 实际验证 | 结果 |
| --- | --- |
| 原隔离环境 Python 3.12.13，HTTP/job/models 专项 | 185 passed，2.83 秒 |
| 原隔离环境 `python -m pytest -q tests/test_factory_*.py` | 660 passed，1 条未屏蔽 fork 警告，84.68 秒 |
| Node 22.16.0 `node --test tests/test_factory*.mjs` | 54 passed，0 skipped |
| `python scripts/browser_fixtures.py` | CPU 合成媒体成功，无模型调用 |
| `npm ci --ignore-scripts` | 原机器安装成功但 Node 16 engine 警告；测试改在 Node 22 执行 |
| Chromium 144 本地 `npm run test:browser -- --config=playwright.audit.config.mjs` | 8 项被 ERR_BLOCKED_BY_ADMINISTRATOR 阻断，blocked；未绕过策略 |
| 恢复工作区 Python 3.12.3 专项 / 全量 | 185 passed / 660 passed（74.96 秒） |
| 恢复工作区 Node 22.23.2，npm 12.0.2 | npm ci 成功；54 passed，0 skipped；CPU 媒体 SHA 与原运行一致 |
| `git diff --check` | 已执行，提交前再核验 |

原容器 npm 下载遇到 EAI_AGAIN；锁定依赖在隔离工作树安装后转入 Node 22 容器。浏览器临时配置仅指定已有 Chromium，没有改断言、重试、超时或管理策略。原环境缺少的评测依赖仅装进忽略的 runtime 目录；恢复工作区使用独立 venv，不改生产 Python。

恢复时依赖工具首次选择了 Python 3.10.20，专项通过但全量因原代码使用 Python 3.12 f-string 语法出现 9 项收集错误。未修改原业务代码或测试绕过错误；另建明确 Python 3.12 的 venv 后按原命令全量通过。

CPU 视频 SHA-256 `c45a53d8566af65486a063366fdb0a98316ef99465a65614572b074fe2da4dc8`；帧图 `93585e916ad2fb5f50d875ddb44f8b1b4fa55bf3693c049d336a1594a765d9c9`。

恢复导出 SHA-256：源码 a1230a7609a2b762701971036089c0c0cf1857d8dbfb391ef60ff1458eefb556；浏览器增量 9c2ce92a49b7cc0c8e260597d61bb7899dcb46397aba23381218c12348e780ca。按精确文件清单恢复，再补回最终已测试的 Legacy 500 回归，不覆盖原工作树。

GPU、真实云/模型、艺术质量、外部认证代理、现场部署均 not_run。其他工作包尚无提交或 PR，见 [审查矩阵](NEXT_ROUND_AUDIT.md)。

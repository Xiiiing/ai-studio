# 下一轮 PR 交付报告

基线：`4aaece74ab54f38c125c564accc20f4134fb5bab`。本文件在取得真实远端结果后更新，不预填 PR 编号或 URL。

## 工作包 7

分支：`feat/http-security-hardening`；base：`main`。状态：implemented、tested（Python、Node、真实浏览器）；pushed、draft_pr_open、ci_passed 暂为 not_run。Shell Git 推送预检因缺少凭据 blocked；GitHub 连接器已确认 push 权限，远端提交与 PR 尚待实际完成。

主要文件：`studio/http_routes.py`、`studio/http_security.py`、`studio/http_media.py`、`studio/web.py`、`factory/web.py`、`factory/job_api.py`、`factory/store.py`；对应 Python/浏览器测试与安全审查文档。未修改云提交、账本、GPU Lease 或配置 Schema。

### 实际验证

| 命令 / 环境 | 结果 |
| --- | --- |
| Python 3.12.13：`python -m pytest -q tests/test_factory_http_security.py tests/test_factory_job_api.py tests/test_factory_web_models.py` | 188 passed，2.89 s |
| Python 3.12.13：`python -m pytest -q tests/test_factory_*.py` | 663 passed，80.39 s；1 条多线程 fork 警告，未忽略 |
| Node 24.21.0 / npm 11.19.0：`npm ci --ignore-scripts --offline` | 成功；固定锁文件，从隔离工作树缓存安装，无外网 |
| 同一隔离容器：`node --test tests/test_factory*.mjs` | 54 passed，0 skipped |
| `python scripts/browser_fixtures.py` | CPU 合成测试媒体成功；未运行 GPU/云模型 |
| Playwright 1.64.0 固定镜像：`npm run test:browser` | 8 passed，18.4 s，0 skipped；原配置、原超时、无重试 |
| `git diff --check`、源码差异检查 | 已执行；最终提交前再核验 |

最终镜像：`mcr.microsoft.com/playwright@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f`。容器设置 `--network=none --cpus=2 --memory=4g --pids-limit=384 --shm-size=1g`，无端口发布、无 GPU、无 Docker socket、无生产数据库挂载。只读挂载现有 Python 解释器与控制依赖，工作树内 CPU 测试夹具可写。未修改生产 Python 环境；缺少的 opencc/pypinyin 位于忽略的 `runtime/audit-tools/python`，通过 PYTHONPATH 使用。

保留的失败证据：早期环境 DNS/管理策略阻断；新增嵌入式监听测试修复前 3 failed；首次真实浏览器因 CORP 隐藏 response 事件而 1 failed；新增服务端证据输出的并发问题导致 2 failed。后两项修复的是测试观察与输出同步，最终同时验证 browser blocked、实际 HTTP 403、launch_attempts=0，没有跳过失败用例。完整说明见 HTTP 安全审查表。

可复核日志：`runtime/audit-evidence/http-revalidated.log`、`python-revalidated.log`、`browser-container-final.log`；浏览器 JSON：`runtime/browser-report/results.json`。这些本地日志与合成媒体被 Git 忽略，不作为生产数据提交。

CPU 视频 SHA-256：`c45a53d8566af65486a063366fdb0a98316ef99465a65614572b074fe2da4dc8`；帧图 SHA-256：`93585e916ad2fb5f50d875ddb44f8b1b4fa55bf3693c049d336a1594a765d9c9`。

真实模型、GPU/云端生产、外部认证代理和现场部署均 not_run。浏览器通过只证明真实浏览器与本地 Python/Mock 后端交互，不证明生成画面达到生产质量。其他工作包尚无提交或 PR 交付记录；见 [审查矩阵](NEXT_ROUND_AUDIT.md)。

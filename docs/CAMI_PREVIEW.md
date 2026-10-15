# cami 新版界面预览

当前开发机的预览采用真实 Python Web/API 和独立 SQLite 数据库，前端为当前 React 构建。入口为 <http://127.0.0.1:8787/>，在 cami 本机浏览器打开。远程电脑可使用已有授权 SSH 身份建立同端口转发后访问本机地址：

```sh
ssh -N -L 8787:127.0.0.1:8787 <已有授权的 cami SSH 主机别名>
```

项目使用入口见 [README](../README.md)，新版改动与验证范围见 [升级报告](STUDIO_UPGRADE_2026-10-15.md)。

服务管理：

```sh
.venv/bin/python scripts/deploy_preview.py start
.venv/bin/python scripts/deploy_preview.py status
.venv/bin/python scripts/deploy_preview.py stop
```

预览的发布快照、配置和数据库位于 `runtime/cami-preview/`，由用户级 systemd 管理，使用 Python 服务同源提供静态资源。日志通过 `journalctl --user -u ai-studio-preview.service` 查看。它不替换 cami 现有服务，也不连接 Wkp 的数据库、素材或模型。不启动 worker，所有模型配置禁用，因此不会执行生成或产生供应商费用。部署更新复制上一版预览媒体并沿用预览数据库；提交的决定不会在重启时被样例覆盖。

预置五个标题含“示例”的项目，分别演示故事比较、独立审稿返修、视觉候选与全部否决、终片确认与返修，以及补充素材／切回原创。审核结论和 Agent 任务记录明确标注为模拟数据，没有供应商调用回执；视觉为 CPU 绘图，视频为六秒测试图案。这些素材只能说明页面、播放器和交互效果，不代表模型生成质量。人工操作会正常写入预览数据库；选择或修改后产生的队列不会继续运行。

`scripts/seed_preview.py` 仅允许在 `runtime/cami-preview/` 内的发布目录运行，必须显式提供该工作区内的 `DRAMA_FACTORY_DATA`、`DRAMA_FACTORY_CONFIG` 绝对路径，配置中模型必须全部禁用。样例按固定请求标识幂等创建，重复执行会保留已有项目及其人工修改。它不读取测试目录、不访问网络、不启动模型，也不能向正常工作区播种。

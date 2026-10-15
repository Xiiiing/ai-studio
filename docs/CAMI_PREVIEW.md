# cami 新版界面预览

当前开发机的预览采用真实 Python Web/API 和独立 SQLite 数据库，前端为当前 React 构建。入口为 <http://127.0.0.1:8787/>，在 cami 本机浏览器打开。远程电脑可使用已有授权 SSH 身份建立同端口转发后访问本机地址：

```sh
ssh -N -L 8787:127.0.0.1:8787 <已有授权的 cami SSH 主机别名>
```

局域网可直接打开 <https://172.20.12.202:8788/>，使用账号 `preview`；随机密码和证书 SHA256 指纹位于本机私有 `runtime/cami-preview/lan/ACCESS.txt`（权限 600，不进入 Git）。首次访问需要信任该入口的自签名证书，或在访问设备安装 `runtime/cami-preview/lan/172.20.12.202-certificate.pem`。该入口独立使用 Caddy 2.8+，监听指定内网 IPv4，HTTPS、Basic Auth、原始 Host/Origin/Fetch Metadata 校验均在转发之前执行；正常同源 Origin 才会转换为 loopback 上游来源，重复或异常 Origin 仍由上游拒绝。实现依据 [Caddy 认证文档](https://caddyserver.com/docs/caddyfile/directives/basic_auth)和[代理请求头文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers)。

后端继续监听 loopback，不修改 `studio.http_security` 的准入。网关不占用已有服务的 80/443 端口，不读取其他应用的证书或凭据。cami 复用已安装的 Caddy 2.11.4 镜像中的程序，在 `runtime/cami-preview/lan/bin/caddy` 运行独立用户服务；其他机器可自行安装 Caddy 或将程序放到同一路径。

项目使用入口见 [README](../README.md)，新版改动与验证范围见 [升级报告](STUDIO_UPGRADE_2026-10-15.md)。

服务管理：

```sh
.venv/bin/python scripts/deploy_preview.py start
.venv/bin/python scripts/deploy_preview.py status
.venv/bin/python scripts/deploy_preview.py stop
.venv/bin/python scripts/preview_lan.py start
.venv/bin/python scripts/preview_lan.py status
.venv/bin/python scripts/preview_lan.py stop
```

网关日志：`journalctl --user -u ai-studio-preview-lan.service`。停用预览时需要分别停止 Web 与网关；修改后端端口后重新启动网关以更新上游地址。网关默认端口 8788，可用 `--host`、`--port` 指定其他内网地址和非特权端口。

预览的发布快照、配置和数据库位于 `runtime/cami-preview/`，由用户级 systemd 管理，使用 Python 服务同源提供静态资源。日志通过 `journalctl --user -u ai-studio-preview.service` 查看。它不替换 cami 现有服务，也不连接 Wkp 的数据库、素材或模型。不启动 worker，所有模型配置禁用，因此不会执行生成或产生供应商费用。部署更新复制上一版预览媒体并沿用预览数据库；提交的决定不会在重启时被样例覆盖。

预置五个标题含“示例”的项目，分别演示故事比较、独立审稿返修、视觉候选与全部否决、终片确认与返修，以及补充素材／切回原创。审核结论和 Agent 任务记录明确标注为模拟数据，没有供应商调用回执；视觉为 CPU 绘图，视频为六秒测试图案。这些素材只能说明页面、播放器和交互效果，不代表模型生成质量。人工操作会正常写入预览数据库；选择或修改后产生的队列不会继续运行。

`scripts/seed_preview.py` 仅允许在 `runtime/cami-preview/` 内的发布目录运行，必须显式提供该工作区内的 `DRAMA_FACTORY_DATA`、`DRAMA_FACTORY_CONFIG` 绝对路径，配置中模型必须全部禁用。样例按固定请求标识幂等创建，重复执行会保留已有项目及其人工修改。它不读取测试目录、不访问网络、不启动模型，也不能向正常工作区播种。

# cami 内网真实制作入口

cami 的新版界面通过独立 HTTPS 网关接入 **Wkp 上的 Web、SQLite、worker 和模型**。这不是在 cami 启动一个模型 worker，也不复制 Wkp 的配置、数据库或素材。

| 入口 | 用途 | 状态来源 |
| --- | --- | --- |
| `https://172.20.12.202:8788/` | 独立示例预览 | cami 示例数据库，无生成 worker |
| `https://172.20.12.202:8789/` | 真实创作、审核和制作 | Wkp 的生产数据库、worker 与模型 |
| `https://172.20.114.10:8787/` | 原 Wkp 入口 | 同一份生产状态，原认证保留 |

真实入口的操作会修改 Wkp 项目。新增费用授权、预算增加和最终成片确认仍由用户决定。为避免误操作，第一次验收应创建有明确联调名称的新项目，保留旧项目不动。

## 接入方式

```text
浏览器 → cami :8789（HTTPS + 独立 Basic Auth）
       → Wkp :8790（HTTPS + cami 客户端证书认证）
       → Wkp 127.0.0.1:8787（factory.web）
       → Wkp 持久化队列 / worker / Qwen3.8 / Qwen3-TTS / MiniMax
```

cami 只持有为此入口新建的网关登录信息和专用 TLS 客户端私钥。Wkp 的既有 Web 密码、供应商 Token、模型密钥和服务凭据不复制到 cami。客户端证书是公钥材料，可以交给 Wkp 管理入口；客户端私钥必须留在 cami。

cami 先对原始 Host、Origin 和 Fetch Metadata 做检查，再进行 Basic Auth 和代理转发。向 Wkp 转发时删除浏览器 Authorization，精确转换 Host 和同源 Origin；重复或其他来源的 Origin 不整体替换成合法值。Wkp 入口仍需再次检查 Host、Origin、重复值和来源，最后转换为 Python 服务的 loopback 同源地址。原 Wkp 的 HTTPS 入口及认证不受影响。

## 准备与启动

在 cami 仓库执行：

```bash
.venv/bin/python scripts/wkp_live_gateway.py prepare
```

该命令只准备本地网关身份，不启动服务。它从 Wkp 原 8787 入口获取公开证书，必须匹配 [交接文档](WKP_REMOTE_DEV_HANDOFF.md) 中记录的 SHA-256 指纹才保存为受信证书。证书续期导致指纹变化时，先通过授权管理入口独立核实，不能关闭 TLS 验证。

私有文件位于 Git 忽略的 `runtime/wkp-live-gateway/`，目录权限 `0700`，身份文件 `0600`：

- `client-certificate.pem`：交给 Wkp 专用 8790 入口信任的公开客户端证书。
- `client-private-key.pem`：仅 cami 使用的客户端私钥，不传输。
- `wkp-server-certificate.pem`：匹配交接指纹的公开 Wkp 服务器证书。
- `172.20.12.202-certificate.pem` 和对应私钥：浏览器到 cami 的 TLS。
- `credentials.json`、`ACCESS.txt`：新建的真实入口登录信息，控制台不打印密码。

Wkp 管理员使用 `scripts/wkp_cami_gateway.py --client-sha256 <公开证书 PEM 文件的 SHA256>` 配置独立 8790 入口，通过标准输入传入 `client-certificate.pem` 的公开内容。该校验值是 PEM 文件字节哈希，区别于准备命令返回的 DER 证书指纹。脚本复用 Wkp 原服务器证书路径和 nginx 程序，创建独立 `ai-studio-cami-gateway.service`，只允许 cami 的 IP 和客户端证书身份，并保留原生产 Web 的 loopback 绑定。既有 Wkp 网关不重载、不更改密码；不得以放开 Python 的 LAN 绑定替代网关。

确认 Wkp 8790 配置完成后，在 cami 执行：

```bash
.venv/bin/python scripts/wkp_live_gateway.py start
.venv/bin/python scripts/wkp_live_gateway.py status
```

启动会先通过双向 TLS 验证真实 `/api/health`，再验证 Caddy 配置并启用用户级 `ai-studio-wkp-live.service`。需要已安装 Caddy 2.11+，或已存在的 `runtime/cami-preview/lan/bin/caddy`；脚本不会下载可执行文件。无关服务占用同名 unit、符号链接私有路径或过宽的凭据权限都会拒绝处理。服务启动失败时恢复此前的配置和运行状态。

查看 `runtime/wkp-live-gateway/ACCESS.txt` 获取登录信息和 cami 证书指纹。浏览器第一次访问自签名证书时核对该指纹。TLS 身份有效期为 90 天，到期前由管理员协调更新客户端信任和浏览器证书。

停用真实入口：

```bash
.venv/bin/python scripts/wkp_live_gateway.py stop
```

停用仅影响 cami 专用网关，不停止 Wkp Web、worker、模型或示例预览。

## 验证边界

自动化检查：

```bash
.venv/bin/python -m pytest tests/test_factory_wkp_gateway.py -q
```

本地测试覆盖证书指纹拒绝、私网绑定限制、凭据与符号链接保护、同名服务保护、上游不可用时保留部署以及失败恢复。模板另通过实际 Caddy 二进制验证。上线还需实测未登录、错误 Host/Origin、重复 Origin、视频 Range 和浏览器提交。

## 本次部署与恢复

Wkp 已更新到已合并主线 `main@1a183f7ae1cbfcc82e0bfca0011144ec4cdf5b72`，Web 版本 `3.0.0`。升级工具 `scripts/wkp_deploy_release.py` 要求精确 commit 和 UI 包 SHA256，拒绝非主线提交、未提交修改和未结束任务；先关闭 Web 收件，再确认队列为空，才停止 worker，避免预检期间新到的任务被杀掉。升级前在 Wkp 本地备份两个 SQLite、配置和旧 UI，并用本地 tag 保存原源码；失败时恢复源码、UI 和服务，保留数据库的新记录。

本次回退标记为 `deploy/wkp-before-20261015T140032Z`，备份为 Wkp 的 `runtime/deployments/20261015T140032Z-1a183f7a/`。两个原项目继续保留，私有配置未变，systemd 模型 Credential drop-in 继续生效。

当前真实模型是主 Qwen3.8、独立审稿 Qwen3.8 和既有 Qwen3.6 视觉审核服务；配音由 Wkp 的 Qwen3-TTS 本地进程执行。当前配置只有本地视频适配器，没有 MiniMax 连接；首条真实验收使用动态漫路线，调用真实图像与配音后进行程序运镜和合成，不产生云视频费用。

通过健康接口只能证明网关和 Web 可用。真实模型全流程验收要在 Wkp 实际完成创作、独立审核、视觉、镜头生成、配音、合成和终审，并检查产物、调用记录与费用；不能用示例预览或本地 mock 测试代替。

Caddy 代理 TLS 字段按 [官方 reverse_proxy 文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#the-http-transport) 配置。

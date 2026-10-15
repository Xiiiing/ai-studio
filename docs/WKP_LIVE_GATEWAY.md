# cami 测试入口与 Wkp 推理服务

当前部署边界：**整个工作流系统在 cami，Wkp 只提供模型推理 API**。

| 服务器 | 运行内容 |
| --- | --- |
| cami `172.20.12.202` | 前端、业务 API、SQLite 项目库、LangGraph 检查点、工作流 worker、Agent 编排、预算、质检与 FFmpeg 剪辑 |
| Wkp `172.20.114.10` | Qwen 模型服务及低层图像、视频、Qwen3-TTS 推理服务；不执行项目工作流 |

用户统一访问 `https://172.20.12.202:8789/`。cami 原示例 8787 / 8788 服务已停止；现在 8787 仅作为 cami 本机业务 API 的 loopback 端口，不开放给局域网。

Wkp 8790 是现有的 mTLS 内部模型入口，只允许 cami 的 IP 和客户端证书，通过 `/inference/` 调用 loopback 8791 推理进程。该入口不再代理工作台或项目 API；其他路径返回 404。没有新增用户访问入口。

cami 使用 `runtime/cami-studio/data/` 新建独立数据库。没有复制、清空或迁移 Wkp 旧项目、数据库、素材、权重或原模型凭据。Wkp 的旧业务 Web 和工作流 worker 在安全边界停用，原始数据保留。

配置与启动步骤见 [cami 控制层部署](CAMI_CONTROL_PLANE.md)。前端仍由当前代码构建，不运行 Node 生产服务。

```bash
npm run build
.venv/bin/python scripts/deploy_cami.py start
.venv/bin/python scripts/wkp_live_gateway.py start
```

登录账号为 `studio`，密码与浏览器证书指纹保存在 Git 忽略的 `runtime/wkp-live-gateway/ACCESS.txt`。已有登录身份保留，客户端私钥只留在 cami，Wkp 模型凭据只留在 Wkp。

推理任务具有持久 ID 和结果哈希。cami 负责保存请求、恢复续查、校验并保存产物与实际 GPU 用量；Wkp 的持久记录只用于推理去重和故障恢复，不包含项目决策或 LangGraph 状态。首次新生成前需核对项目内旧任务，不能把尚未确认的生成当作失败后再次提交。

当前已适配既有 Qwen、FLUX、Wan 和 Qwen3-TTS 调用链。云端模型与视频供应商通过控制层的供应商适配器接入，费用授权仍在 cami。未配置的供应商不能视为已连通或已验收。

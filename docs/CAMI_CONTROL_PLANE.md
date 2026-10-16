# cami 完整工作流控制层

cami 运行 AI Studio 的 Web、工作流 worker、角色编排、项目数据库、审批、费用确认和素材管理。Wkp 提供模型与 GPU 推理 API；cami 不再代理 Wkp 的旧版工作流系统。

```text
浏览器 → cami LAN HTTPS :8789 → cami Web 127.0.0.1:8787
                                  ↓ 本机数据库 / 本机 worker
                             Wkp mTLS :8790/inference
                                  ↓ Wkp GPU / 模型推理
```

LAN 入口由独立的 `scripts/wkp_live_gateway.py` 管理，本启动器只管理 Web 和 worker，不启动旧的 8788 服务。

当前内网测试入口免登录，使用 `.venv/bin/python scripts/wkp_live_gateway.py start --auth-mode none` 启动。唯一浏览器地址仍为 `https://172.20.12.202:8789/`，不改变 Wkp 模型服务的 mTLS 鉴权。详见 [入口配置](WKP_LIVE_GATEWAY.md)。

## 独立运行目录

`scripts/deploy_cami.py` 使用仓库下的 `runtime/cami-studio/`：

| 路径 | 内容 |
| --- | --- |
| `config.json` | 从公开 `config/factory.example.json` 创建的独立配置 |
| `data/` | 新数据库、项目、worker 心跳和新生成素材 |
| `service.env` | 本机路径、TLS 环境映射及运行环境 |

只迁系统。启动器不读取旧私有配置，不复制或导入旧项目、数据库、素材、记忆和费用授权，也不创建演示项目。首次准备时 `data/` 为空，应用启动后按正常流程初始化新数据库。旧目录保持原状。已有 cami 配置及模型编辑保持原字节；控制层或 GPU 路径不兼容时明确拒绝启动，不静默覆盖。

## 推理配置

| Profile | Wkp 模型 | 上下文 | 用途 |
| --- | --- | --- | --- |
| `primary` | `qwen3.8-27b-q8-opt-2dflash` | 32768 | 默认角色及 executive producer |
| `critic` | `ai-studio-qwen38-23` | 16384 | independent critic、story critic |
| `vision` | `Qwen3.6-35B-A3B-UD-Q4_K_S` | 32768 | visual critic |

2026-10-16 通过现有 mTLS 入口读取三个服务的 `/v1/models` 与 `/props`：两个 Qwen3.8 服务均声明 `vision: false`，Qwen3.6 声明图片和视频输入。因此保留 Qwen3.6 承担看图审核。它不生成图像或视频；H3 自带的 Qwen3-VL 编码器也是另一项独立依赖。主服务实际单槽上下文为 131072，控制层仍使用较保守的 32768 上限。

文本接口使用 `llama_cpp`、`llama_json_schema` 和 `llama_tokenize`，地址为 `https://172.20.114.10:8790/inference/models/{profile}/v1`。Wkp 代理提供对应的模型、模板和 tokenize 路由。cami 使用既有客户端证书进行 mTLS 认证，不持有或复制 Wkp 模型服务的 Bearer 凭据。

以下变量由 `service.env` 映射到本仓库 `runtime/wkp-live-gateway/` 已存在的本机文件：

| 环境变量 | 文件 |
| --- | --- |
| `CAMI_WKP_TLS_CA` | `wkp-server-certificate.pem` |
| `CAMI_WKP_TLS_CERT` | `client-certificate.pem` |
| `CAMI_WKP_TLS_KEY` | `client-private-key.pem` |

生产任务使用 `production.gpu_service` 的 `https://172.20.114.10:8790/inference` 及同一组本机 TLS 文件。`gpu_cards: [4,5,6,7]` 是 Wkp 的远端 GPU 编号。

新建控制层配置绑定 `video_generator → wkp_h3`：`provider: comfyui_h3`、`model: MiniMax-H3`。沿用 Wkp 原有 ComfyUI 双卡 H3 主模型、Qwen3-VL 编码器、视频 VAE 和 turbo4 LoRA，4 步采样，首帧图生视频，24 fps、`17k+5` 帧数。生成画布默认 352×640，保留原模板 640×352 的像素量并适配竖屏；交付仍按项目规格剪辑。生成音频不进入此图，配音继续走 Qwen3-TTS。此连接不调用 MiniMax 云 API、不产生云费用授权，也不自动回退到 Wan。

**已有安装不会由 `prepare/start` 自动改写。** 2026-10-16 初次核实时，Wkp 入口仅开放 FLUX/Wan 图；随后经用户授权，已发布 H3 支持、切换 cami 并通过独立真实生成及恢复验收，见 [验收记录](WKP_H3_MODEL_AUDIT.md)。其他安装切换时，先部署支持 H3 的推理服务版本，再执行以下只读检查：

```bash
.venv/bin/python scripts/configure_wkp_h3.py
```

检查要求远端健康信息明确报告 H3 的模型、工作流版本、依赖和双卡支持，并重新读取三个文本/视觉服务的真实模型名及能力；旧版仅返回 `models.video: true` 不能通过。显式切换时先确认无未完成任务，停止 cami Web 和 worker，再运行 `scripts/configure_wkp_h3.py --apply`。该命令保留其他配置，停用旧 Wan 连接，选择 H3；不会重启服务。之后按已批准的发布流程启动 cami 并进行独立生成验收。未确认的旧任务必须先核对，不重放、不修改原项目数据。详见 [H3 核实与接入记录](WKP_H3_MODEL_AUDIT.md)。

## 准备与运行

创作审核与自动返修采用 `creative.max_review_rounds`，默认每批最多 4 轮审核（3 次返修），支持整数 1–6。每轮保留前稿与独立审核意见，合格后立即停止。达到上限时，已配置的 `executive_producer` 通过持久化任务提供实际模型建议；用户采纳后通过原有反馈、版本校验和独立复审继续。模型未配置或建议请求结果不确定时保留人工处理入口，不自动重复调用。已有显式上限不会因升级被静默改写；本次 cami 测试配置按用户需求设为 4。

先按 [部署说明](DEPLOYMENT.md) 安装本仓库 `.venv` 和 UI 依赖并运行 `npm run build`，准备上述既有 TLS 文件。执行命令的用户需要可用的 systemd user session。

```bash
.venv/bin/python scripts/deploy_cami.py prepare
.venv/bin/python scripts/deploy_cami.py start
.venv/bin/python scripts/deploy_cami.py status
.venv/bin/python scripts/deploy_cami.py stop
```

`prepare` 校验 UI、Python 和 TLS，创建独立配置与空数据目录，不启动服务。`start` 创建并启用 `ai-studio-web.service` 和 `ai-studio-worker.service`，工作目录均为本仓库，执行本机 `.venv/bin/python -m factory.web` / `-m factory.worker`。EnvironmentFile 提供 `DRAMA_FACTORY_CONFIG`、`DRAMA_FACTORY_DATA`、`DRAMA_MEDIA_PYTHON` 和 TLS 映射。

运行目录权限为 700，配置及环境文件为 600，私钥必须仅当前用户可读。启动器拒绝符号链接、其他用户所有的文件以及缺少当前仓库所有权标记的同名服务；包括从其他 systemd 搜索路径安装的同名服务。

`start` 在停止原服务前检查未完成任务，停止 Web 接收新任务后再次检查；有任务时保留或恢复原服务。启动失败会恢复此前的 unit 内容、启用状态和运行状态。就绪检查同时核对 Web 健康状态以及本次 systemd worker PID 对应的新鲜心跳。不会通过强杀正在推理的 worker 完成部署。

`stop` 同样拒绝存在未完成任务的情况。先在工作台完成或取消相关任务，再停止服务；只暂停项目可能仍保留排队任务，应以 `status` 的 `active_jobs: 0` 为准。停止服务保留新数据库、配置和素材，且不管理 LAN gateway。

日志检查：

```bash
journalctl --user -u ai-studio-web.service -u ai-studio-worker.service -n 100 --no-pager
```

本地测试使用临时目录、假 systemd 和 TLS 测试替身，不操作真实服务或生产数据：

```bash
.venv/bin/python -m pytest -q tests/test_factory_cami_deploy.py
```

# AI Studio：Wkp 内网服务器与异机开发交接

> 核实日期：2026-10-15（中国标准时间，UTC+08:00）。适用场景：**把代码开发迁到新服务器，但保留 Wkp 上的模型、Web、Worker、项目数据与 GPU 生产服务**。本文件只提供地址、权限边界和操作方法，**不包含任何登录密码、API Token 或 SSH 私钥**。

## 1. 先读这一页：入口和拓扑

| 项目 | 已确认的值 | 用途与限制 |
| --- | --- | --- |
| Wkp 服务器名 | `sz-PR4910P`；Tailscale 显示名 `wkp` | 运行现有短剧工作室 |
| 机房局域网 | `172.20.114.10/24`（网卡 `eno0np0`） | 新服务器到该网段**有路由且 ACL 允许**时使用 |
| Tailscale IPv4 | `100.86.17.93/32` | **同一 Tailnet、ACL 允许**时使用；不代表公网地址 |
| SSH | TCP `22`，`ssh.service` 活跃 | 需要实际获批的 SSH 账号、钥匙或其他认证；未验证新服务器的授权情况 |
| 正式 AI Studio HTTPS | `https://172.20.114.10:8787/` | 自签名 TLS + HTTP Basic Auth；正常浏览器和 REST API 首选 |
| Tailscale HTTPS | `https://100.86.17.93:8787/` | 与上面是同一服务的另一个私网入口 |
| Web 内部后端 | `http://127.0.0.1:8787/` | **仅 Wkp 本机**；没有代理层登录保护，不允许直接开放到内网或公网 |
| 项目工作根目录 | `/root/jialin/drama_studio` | 当前以 `root` 运行服务；其他 SSH 用户需经管理员授权才能访问 |
| 数据与检查点 | `/root/jialin/drama_studio/runtime/factory/studio.sqlite3`、`checkpoints.sqlite3` | 唯一生产状态权威；不可直接替换或复制正在写入的 SQLite 文件 |
| 生成素材 | `/root/jialin/drama_studio/production/` | 已有作品、镜头、音频与交付文件，禁止部署时清理 |
| 私有模型设置 | `/root/jialin/drama_studio/config/factory.json` | 服务器私有配置；不提交 Git、不复制凭据到新服务器 |

```text
新的开发服务器（开发／测试／Git）
      │
      ├── SSH :22 ────────────────────┐
      └── HTTPS :8787（Basic Auth）───┤
                                     ▼
                          Wkp / sz-PR4910P
                       172.20.114.10 / 100.86.17.93
                                     │
                         本机 nginx 私网 HTTPS 网关
                                     │
                           127.0.0.1:8787
                           factory.web（HTTP API）
                                     │
                 SQLite（项目／审核／工单／检查点）
                                     │
                     factory.worker（持久化任务消费者）
                            │                  │
                   GPU 0–3：文本模型      GPU 4–7：按需生成
                            │                  │
                       Qwen3.8 × 2          FLUX / Wan / TTS
```

**职责划分**：新服务器开发、运行单元测试及浏览器模拟测试；Wkp 保持正式服务、业务数据、GPU 调度和模型鉴权。默认**不将模型权重、`config/`、`runtime/`、`production/`、密钥或正在使用的数据库复制到新服务器**。

## 2. 在新服务器检查是否连通

不确定新服务器在不在相同网络，先测试，不要因为 IP 相似就假设互通。建议顺序：

```bash
# 路由与 TCP（若 nc 未安装，可用 bash /dev/tcp 或 curl 替代）
ip route get 172.20.114.10
nc -vz -w 5 172.20.114.10 22
nc -vz -w 5 172.20.114.10 8787

# 如果新服务器已经加入相同的 Tailscale 网络：
tailscale status
tailscale ping 100.86.17.93
nc -vz -w 5 100.86.17.93 22

# 没有登录凭据时，HTTPS 的 401 代表入口可达且要求认证；不是服务崩溃。
curl --silent --show-error --connect-timeout 5 --output /dev/null \
     --write-out 'HTTP %{http_code}\n' https://172.20.114.10:8787/api/health
# 上述 curl 可能因自签名证书失败；请按下一节验证并信任证书。
```

**常见原因**：无法连上 `172.20.114.10` 多是跨网段路由／VLAN 或防火墙 ACL；Tailscale 不通可能是设备未加入同一 Tailnet 或被 ACL 拦截；SSH TCP 通不代表具有登录权限。若你提供新开发服务器 IP／SSH 用户，我方才能进一步实际验证双向连通。

### SSH 配置（示例，必须替换占位符）

新服务器的 `~/.ssh/config`：

```sshconfig
Host wkp
    HostName 172.20.114.10
    Port 22
    User YOUR_AUTHORIZED_WKP_USER
    IdentityFile ~/.ssh/id_ed25519_wkp
    IdentitiesOnly yes
    StrictHostKeyChecking ask

# 同一 Tailnet 但不在机房局域网时，可改用：
Host wkp-ts
    HostName 100.86.17.93
    Port 22
    User YOUR_AUTHORIZED_WKP_USER
    IdentityFile ~/.ssh/id_ed25519_wkp
    IdentitiesOnly yes
    StrictHostKeyChecking ask
```

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/config ~/.ssh/id_ed25519_wkp   # 私钥若存在才执行该命令
ssh -o BatchMode=yes wkp 'hostname; whoami; uptime'
```

**已实测 Wkp SSH 服务端 Ed25519 主机公钥指纹：**

```text
SHA256:S+2QrGtK975LiOe6WwcCrbfLq0McmClBkd9b823n2+4
```

首次连接应与该指纹核对，**不要关闭 `StrictHostKeyChecking`**。Wkp 目前 SSH 配置允许公钥登录；`root` 登录仅允许公钥、不允许密码。命令中出现的 `YOUR_AUTHORIZED_WKP_USER` 未经确认，**不代表该用户已存在或已授权**。当前部署目录在 `/root` 下，普通用户须由管理员设置适当的读写／sudo 权限，不能据此假设能直接管理服务。

## 3. 从新服务器安全访问 Web / REST API

### 3.1 校验证书，再调用 API

Wkp 使用自签名证书，证书覆盖 `172.20.114.10` 与 `100.86.17.93`，当前有效期至 **2027-10-15 09:17:43 UTC**（证书续期后指纹会改变）。

**已实测证书 SHA-256 指纹：**

```text
95:3D:3F:0A:15:F0:18:6E:39:AB:DC:1B:3D:50:7B:AF:48:AD:66:4F:F5:22:AB:A0:F3:8A:E8:21:22:84:52:AC
```

在新服务器先获取**公钥证书**并验证指纹（不是私钥）：

```bash
openssl s_client -connect 172.20.114.10:8787 \
  -servername 172.20.114.10 -showcerts </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > ./wkp-lan.crt

openssl x509 -in ./wkp-lan.crt -noout -fingerprint -sha256
# 必须人工比对上面的受信指纹，匹配后才使用 --cacert。
```

HTTP Basic Auth 用户名是 `studio`；**密码不写在文档和脚本里**，向 Wkp 管理员通过独立安全渠道获取。`curl --user studio` 会在终端提示输入密码。只读示例：

```bash
WKP_BASE='https://172.20.114.10:8787'

curl --fail-with-body --silent --show-error \
  --cacert ./wkp-lan.crt --user studio \
  "$WKP_BASE/api/health"

curl --fail-with-body --silent --show-error \
  --cacert ./wkp-lan.crt --user studio \
  "$WKP_BASE/api/factory/overview" | python3 -m json.tool

curl --fail-with-body --silent --show-error \
  --cacert ./wkp-lan.crt --user studio \
  "$WKP_BASE/api/factory/models" | python3 -m json.tool
```

若通过 Tailscale，把 `WKP_BASE` 换为 `https://100.86.17.93:8787`；使用**对应 IP 作为 Host**，不要手工设置一个不匹配的 `Host` 头。生产接口的跨站与来源校验比较严格。

### 3.2 当前已部署的主要 API

| HTTP | 路由 | 含义 |
| --- | --- | --- |
| GET | `/api/health` | Web 存活探测；不代表 GPU 模型都已准备好 |
| GET | `/api/factory/overview` | 项目列表、队列及资源摘要 |
| GET | `/api/factory/capabilities` | 制作能力与资源状态 |
| GET | `/api/factory/workflow` | 工作流定义及模型角色 |
| GET | `/api/factory/models` | 模型连接清单（不返回密钥） |
| GET | `/api/factory/projects/{项目ID}` | 12 位十六进制项目 ID 的项目状态 |
| GET | `/api/factory/projects/{项目ID}/flow` | 实际任务、Agent 分工及历史记录 |
| GET | `/api/factory/projects/{项目ID}/shots` | 镜头工作台状态 |
| GET | `/api/factory/projects/{项目ID}/series` | 系列资产状态 |
| GET | `/api/factory/projects/{项目ID}/delivery-readiness` | 交付准备状态，不等于平台审核通过 |
| GET | `/api/factory/projects/{项目ID}/cloud-budget` | 云费用与授权账本 |
| POST | `/api/factory/models/test` | 按已保存模型名检测连接／模型目录，不触发生成 |
| POST | `/api/factory/projects` | **创建真实项目并可能排入生产队列** |
| POST | `/api/factory/projects/{项目ID}/decision` | **人工审核决策，可能驱动下一阶段** |
| POST | `/api/factory/projects/{项目ID}/action` | **暂停、恢复、重试和返修等真实操作** |

**默认只用 GET 检查。** 例如 `models/test` 属于 POST，需携带与入口一致的 `Origin` 头，且需要 Basic Auth：

```bash
curl --fail-with-body --silent --show-error \
  --cacert ./wkp-lan.crt --user studio \
  --header "Origin: $WKP_BASE" \
  --header 'Content-Type: application/json' \
  --data '{"profile_id":"qwen38_primary"}' \
  "$WKP_BASE/api/factory/models/test"
```

**不要**在新服务器直接使用 POST 创建项目、批准终片、增加 GPU 预算、授权云服务或重放供应商请求，除非是你明确安排的测试。真实项目状态在 Wkp，不是可随意复位的 Mock。

### 3.3 另一种开发接入：SSH 本地转发

当你已获得 SSH 授权，且只是要在**运行隧道的同一台机器**打开后端时，可用：

```bash
ssh -N -L 127.0.0.1:8787:127.0.0.1:8787 wkp
# 同一台机器的另一个终端／浏览器：
# http://127.0.0.1:8787/
```

隧道左侧端口特意也是 `8787`，因为后端校验 HTTP Host / Origin；**不要简单改成 18787 再以为所有表单 POST 都会通过**。这一通道绕过 nginx 的 Basic Auth，因此**必须保持新服务器端监听 `127.0.0.1`、依赖已认证的 SSH 通道，不得绑定 `0.0.0.0`**。日常远程使用仍应优先走上一节 HTTPS 网关。若浏览器在你的个人电脑上，而 SSH 隧道只开在另一台开发服务器上，`localhost` 指向的是电脑本身，并不是开发服务器。

## 4. Wkp 模型、GPU 和服务归属

| GPU／服务 | 端口或方式 | 现状和权限边界 |
| --- | --- | --- |
| GPU 0–1：既有 `qwen38-opt-2dflash.service` | 8095，认证接口；现见 loopback 和 `172.20.114.10` 绑定 | 主 Qwen3.8 27B；负责 **10 个文本 Agent**；保持原服务归属，不改启动参数 |
| GPU 2–3：`ai-studio-qwen38-23.service` | `127.0.0.1:8097`，认证接口 | 第二套 Qwen3.8 27B；负责 **故事审核和独立审稿两角色** |
| 既有 Qwen3.6 等他人服务 | 与项目私有连接配置对应（视觉审核目前复用已有服务） | **只读依赖，不停止、不修改、不删除权重或共享服务** |
| GPU 4–7 | 本地按需启动 ComfyUI / FLUX / Wan2.2 / Qwen3-TTS | 图像、视频与配音的 GPU 池；空闲时不一定有常驻监听端口 |
| `drama-factory-web.service` | `127.0.0.1:8787` | Web API 及正式工作室前端 |
| `drama-factory-worker.service` | 单消费者 + SQLite | 实际执行、检查点、预算、审核和任务恢复 |
| `ai-studio-lan-proxy.service` | 两个私网 IP 的 HTTPS `8787` | nginx TLS + Basic Auth + Host/Origin 和跨站校验 |

**不要直接把模型 8095/8097 接口暴露到新服务器网络，也不要复制原模型的 Bearer Token。** 现有模型鉴权由 Wkp 上的 systemd `LoadCredential` 提供，相关引用在 `/etc/systemd/system/drama-factory-web.service.d/` 和 `drama-factory-worker.service.d/` 下；Token 仅留在 Wkp 受保护环境。通常从新服务器调用**正式的制片 REST API**即可；确需调用原始模型 API，请先由管理员独立授权并设计受控访问，不要从服务环境中提取密钥。

有 SSH 管理权限时的**只读**检查：

```bash
ssh wkp 'systemctl is-active drama-factory-web.service drama-factory-worker.service ai-studio-lan-proxy.service ai-studio-qwen38-23.service qwen38-opt-2dflash.service'
ssh wkp 'cd /root/jialin/drama_studio && git status --short --branch && git rev-parse HEAD'
ssh wkp 'ss -lnt | grep -E "(:22|:8787|:8095|:8097)"'
ssh wkp 'cd /root/jialin/drama_studio && envs/factory/bin/python -m factory.cli --help'
```

> 上述 `ssh` 命令假定所用 SSH 身份已被允许访问 `/root/jialin/drama_studio`；否则由管理员代为执行。`systemctl` 只查状态，**不要在迁移时重启这些生产服务**。

## 5. 新服务器继续开发：建议直接从 GitHub 获取代码

### 5.1 三个版本必须分清

| 版本 | Git 状态 | 用途 |
| --- | --- | --- |
| **Wkp 当前正式运行版本** | Wkp 本地 `main@3ab3b16e7dc55ad1388ad4e946106699cd117b38` | 正式服务及项目数据；已包含安全的 systemd 模型凭据能力 |
| **远端 GitHub `main`** | 核实为 `0fc46b7b29e333921870eb3c91d0830138605009` | 较旧，**不要直接用它覆盖 Wkp 的新模型路由** |
| **可移交的未完成大重构** | GitHub 分支 `wip/console-refactor-handoff-20261015`（纯代码快照提交 `8c7d2f75f6ee4b33de5d4dc745d1481944830b2a`，此后另加本交接文档） | **迁移到新服务器后继续开发的推荐入口；未部署，不能直接作为发布版** |

两个在 GitHub 发布的分支：

- [`feat/systemd-model-credentials`](https://github.com/Xiiiing/ai-studio/tree/feat/systemd-model-credentials)：模型鉴权适配；远端提交 `7a059ab1b5cc567a9b05d443ffd00bbd85ed791b`。它与 Wkp 正式 `3ab3b16` **文件树相同，但因发布方式不同，提交 SHA 不同**。
- [**`wip/console-refactor-handoff-20261015`**](https://github.com/Xiiiing/ai-studio/tree/wip/console-refactor-handoff-20261015)：34 个源码改动，包含新版 Dashboard、工作流画布、决策中心、Agent 组织、旧版 UI 移除及回归测试。固定的**纯代码快照**是 GitHub `8c7d2f75f6ee4b33de5d4dc745d1481944830b2a`，与 Wkp 最初隔离工作树提交 `2370a0d9d5300887eb782d480f8246a3c488c241` 的 Git tree **完全一致**，树 SHA：`be8c8f93339bd547109749c3d5a05768ab2e3ef4`。**分支最新提交还包含本交接文档，因此分支 HEAD 的整树哈希不等于上述纯代码快照树哈希；此次没有为该重构创建 PR，也没有更新 GitHub 的 `main`。**

在新服务器：

```bash
git clone https://github.com/Xiiiing/ai-studio.git
cd ai-studio
git fetch origin
git switch -c feature/continue-console --track origin/wip/console-refactor-handoff-20261015

git status --short --branch
git rev-parse '8c7d2f75f6ee4b33de5d4dc745d1481944830b2a^{tree}'
# 固定的纯代码快照树应为：be8c8f93339bd547109749c3d5a05768ab2e3ef4
# 当前 WIP 分支 HEAD 另含交接文档，HEAD^{tree} 与纯代码快照哈希不同是正常的。
```

如果仓库是私有的，需由你在新开发服务器配置**自己的** GitHub 授权；不要复制 Wkp 上的 CLI 登录 Token。

### 5.2 不依赖 GitHub 的离线备份（已在 Wkp 生成）

```text
Wkp 上：/root/jialin/drama_studio/runtime/handoffs/20261015-wkp-dev-transfer/console-wip.bundle
大小：62105 bytes
SHA-256：fee3fc51256c41f05ca281fb997489aae44e2ab94a59247931b66173ab70da36
分支：wip/console-refactor-handoff-20261015
```

拥有相应文件读取权限时：

```bash
scp wkp:/root/jialin/drama_studio/runtime/handoffs/20261015-wkp-dev-transfer/console-wip.bundle ./console-wip.bundle
sha256sum ./console-wip.bundle
# SHA-256 必须与上文一致。

git clone https://github.com/Xiiiing/ai-studio.git ai-studio
cd ai-studio
git bundle verify ../console-wip.bundle
git fetch ../console-wip.bundle 'refs/heads/wip/console-refactor-handoff-20261015:refs/remotes/wkp-handoff/console-refactor-handoff-20261015'
git switch -c feature/continue-console refs/remotes/wkp-handoff/console-refactor-handoff-20261015
```

Bundle 是源码快照，**不含**数据库、生产媒体、模型权重或密钥。若 SSH 用户无法访问 `/root/...`，应由管理员**仅转交这份 Bundle**，不要把整个运行目录递给新服务器。

### 5.3 开发环境和测试

参考版本：Python 3.12、Node.js 24（`package.json` 要求 Node >=20），在新服务器使用独立虚拟环境和模拟数据。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-factory.txt
npm ci
npx playwright install chromium

# 无需真实 GPU/模型，优先使用隔离数据跑回归：
.venv/bin/python -m pytest -q tests/test_factory_*.py
npm test
FACTORY_TEST_PYTHON="$PWD/.venv/bin/python" npx playwright test
```

依赖安装如果有环境兼容问题，以项目的 `requirements-factory-lock.txt` 和实际测试依赖为基准核对；不要因为新机器没有 Wkp 的 GPU 就给真实工厂配置开启云模型回退。**测试中不得指向 Wkp 的真实 SQLite 或生产素材目录。**

### 5.4 当前大重构的实际进度（必须继续修复）

Wkp 上的隔离工作树：

```text
/root/.webcodex-worktrees/drama-studio-cfbadeca
Wkp 本地 WIP 提交：2370a0d9d5300887eb782d480f8246a3c488c241
```

已实现但**尚未正式发布**：新的制片总览统计、项目与操作待办、可选工作流节点和负责人、独立审核回执视图、带实际选项的决策页面、审核问题与可编辑反馈、旧版界面和 `/api/start` 退休（旧媒体读取仍保留）、可选 CAS 版本检查。新的只读接口 `/api/factory/console`、`/api/factory/projects/{id}/workspace` **仅存在于 WIP 代码中，当前 Wkp 正式服务不能依赖它们**。

最后一轮实测：**919 项 Python 通过，82 项 Node 通过，14 项 Playwright 中 13 项通过、1 项失败**。未通过项是 `tests/browser/team.spec.mjs`：工作流 Inspector 选中“剧本与分镜”时，模拟适配器只显示“编剧”，未显示应参与交接的“独立审稿”。应修复成员适配与真实回执的对应关系，**不要直接删除该预期掩盖问题**。之后重新运行全部浏览器和真实 Python HTTP 边界测试、人工核对桌面/移动端截图，再考虑发布。

本轮参考的开源源码包括 [Toonflow-app](https://github.com/HBAI-Ltd/Toonflow-app) 的 storyboard team，以及 [React Flow / xyflow](https://github.com/xyflow/xyflow) 的节点与画布交互；借鉴交接、审核以及节点定位方式，**没有增加第二套执行调度器**。LangGraph + SQLite 仍是唯一执行与业务状态权威。

## 6. 下午真实生产项目问题（不要误判为网络故障）

以下是读取 Wkp **实际数据库和日志**所得到的 2026-10-15 晚间快照，状态会继续变化：

| 项目 ID | 当前状态 | 下一步 |
| --- | --- | --- |
| `7c3a454e435c`，《断脉之择》 | `blocked`，制作阶段；S01 图像素材已达到 3 次授权尝试上限；目前记录有 12/15 关键帧、0 段视频 | 须在决策中心展示失败镜头、审核依据、可勾选返修及版本约束。**禁止不经批准自动清零次数或重试收费/不可重放任务** |
| `50a6f807a95b`，《便利店外的旧钥匙》 | `awaiting_final`；15 镜素材和成片已存在 | 等待出品人实际观看后明确“通过／局部修改／重新剪辑”；**系统不能自动批准终片** |

观察时生产任务队列：`running=0`、`queued=0`，失败工单是历史累积数据，不等于有一个当前进程正在失败。迁移与部署前必须**重新读取实时队列**，不可照搬此快照。

在原 Wkp 调试的安全位置：

```bash
# 要有原服务器授权，且仅查看自己项目的日志：
ssh wkp 'sudo journalctl -u drama-factory-worker.service --since "2026-10-15" --no-pager --lines=120'
ssh wkp 'sudo journalctl -u drama-factory-web.service --since "2026-10-15" --no-pager --lines=80'
ssh wkp 'cd /root/jialin/drama_studio && git status --short --branch'
```

日志可能含项目文字与报错证据，**不要将完整日志公开或提交 Git**。实际决策证据优先查看 JSON API 的项目状态、`flow` 和具体镜头，不靠 stdout 推测成功。

## 7. 代码上线、服务重启和数据保护

**本节仅为后续正式部署 SOP，不是这次已经执行的操作。** 迁入新服务器不意味着现在要动生产。

1. 在新服务器完成 Python + Node + Chromium 的全套测试，补充关键操作的真实浏览器测试。新分支中不能包含 `config/factory.json`、`runtime`、`production`、模型文件或私钥。
2. 在 Wkp **再次**检查 `running`、`queued`、云端不确定任务、实际工作目录与 `git status`。存在活跃渲染时不停止 Worker、不强制重放。
3. 取得一致性备份：SQLite 使用 `sqlite3.Connection.backup()` 或 SQLite 官方 Backup API，不在写入期间直接 `cp` 热库；同时保留私有配置、服务 unit、代码提交、模型路由和素材版本。限制备份文件权限。
4. 将已测试的**明确提交**部署到 Wkp。注意：当前 Wkp 本地 `3ab3b16` 和 GitHub 的 `feat/systemd-model-credentials@7a059...` 虽文件树相同，**提交父子关系不同**，因此不能假设从 Wkp 运行 `git pull --ff-only` 就能直接合并远端 WIP。先核对 `git log --graph`、`git diff` 和目标代码树，使用受控部署工作树／合并流程，**不要 `git reset --hard` 覆盖工作树或数据库**。
5. 只在 Worker 无在途任务、审批和成本记录安全时按计划重启必要的 `drama-factory-web.service` / `drama-factory-worker.service`。模型服务及其他用户进程不需要随前端更新而重启。
6. 在两个 HTTPS 入口验证认证、主页、待办选择、项目图、镜头与 Range 视频播放、模型目录、旧版 `410`、以及跨站 POST 拒绝；再开放试用。失败则回滚**代码和配置**，不要用老 SQLite 覆盖试用期间的新决定或账本。

当前历史备份位置：

```text
/root/jialin/drama_studio/runtime/deployments/20261015-team-0fc46b7-lan/
/root/jialin/drama_studio/runtime/deployments/20261015-model-connections/
/root/jialin/drama_studio/runtime/deployments/20261015-two-qwen38-gpu-split/
```

## 8. 下一位开发者开始时的核对清单

- [ ] 新服务器可以通过局域网或同一 Tailscale 网络连上 Wkp 的 TCP 22/8787；已与管理员核对 SSH 主机指纹和 HTTPS 证书指纹。
- [ ] 已取得自己获批的 SSH 用户和 GitHub 凭据；没有复制 Wkp 的模型密钥、Web 密码或服务器私钥。
- [ ] 已检出 GitHub `wip/console-refactor-handoff-20261015`，确认其**固定的纯代码快照提交 `8c7d2f75...`**的 Git tree 为 `be8c8f93339bd547109749c3d5a05768ab2e3ef4`；当前分支 HEAD 额外包含交接文档。
- [ ] 新服务器的测试运行在隔离的数据库和 fixtures 中，不连接 Wkp 的真实生产路径进行写入。
- [ ] 已修复至少 1 项现存浏览器测试失败，验证待办可点击并确认审核意见/版本控制有实际效果。
- [ ] 明确区分“重构 WIP”与“Wkp 当前正式服务”；生产部署前重新检查工单、备份与模型拥有权。

## 9. 还缺少什么信息？

**只有新开发服务器的 IP/SSH Host Alias、该服务器的 SSH 用户、它是否加入 Wkp 的 Tailscale Tailnet，以及你在 Wkp 获授权的 SSH 用户**尚未明确。确认这些信息后，可以对两台服务器做真正的端到端连通检查，并补充最终的 `~/.ssh/config`。不要共享密码或私钥；只需账户名、主机别名及网络环境即可。

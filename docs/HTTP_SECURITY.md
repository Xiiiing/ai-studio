# HTTP 安全边界与任务 API 审查表

工作包 7，`feat/http-security-hardening`，审查基线 `4aaece74ab54f38c125c564accc20f4134fb5bab`。

## 信任模型

这是单用户、loopback 信任域的管理服务，不是公网认证系统。本机进程仍被信任；Host/Origin 不能阻止具有本机执行权限的攻击者。Factory 和 Legacy 共用 `studio.http_security.LocalHTTPBoundary`，在方法分派之前准入，转交父类不再绕过边界。

默认 loopback 不变。Factory 在初始化数据库和监听之前拒绝非 loopback host，没有不安全放行开关。`localhost` 直接归一为 `127.0.0.1`，不查询 DNS；可显式配置 IPv6 `::1`。Legacy 独立入口仍为 `127.0.0.1:8787`。

准入要求 loopback peer、单个 Host 与实际监听端口匹配、缺省或与 Host 精确同源的 HTTP Origin。只接受 localhost、127.0.0.1、[::1] 或实际绑定的 loopback 字面地址；不接受任意解析到本机的域名、尾点域名、用户信息。`Sec-Fetch-Site: cross-site` 和 `same-site` 被拒绝；本地 CLI 无 Origin 仍可访问。不信任 `X-Forwarded-*`。

远程访问优先使用两端端口一致的 SSH 隧道。直接监听 LAN/公网在本版本明确不支持。认证反向代理必须独立实施 TLS、身份、原始 Host/Origin、CSRF 和访问限制，再连接 loopback 上游；不能只删除 Origin 就声称安全。该代理部署未验证。

## 完整路由清单

权威代码为 `studio/http_routes.py`。pid 沿用 12 位小写十六进制，jid 沿用 32 位小写十六进制队列 ID，而非供应商 task_id；slug 和 shot_id 沿用原有规范。所有类别都通过同一准入，读取和媒体不因此公开。未知路由 404，不支持的方法 405。

| 方法 | 路径 | 分级 / 约束 |
| --- | --- | --- |
| GET | `/` | 工作台或独立 Legacy UI |
| GET | `/legacy` | Factory 中的原 Legacy UI |
| GET | `/factory/static/*` | UI；扩展名限制，拒绝符号链接 |
| GET | `/api/health` | Legacy 读取 |
| GET | `/api/episodes` | Legacy 项目清单 |
| GET | `/api/episode/{slug}` | Legacy 项目读取 |
| GET | `/api/jobs` | 原 Legacy 作业清单 |
| GET | `/media/production/*` | 媒体；扩展名白名单、描述符级路径检查、单 Range |
| POST | `/api/start` | Legacy 任务控制；8 KiB JSON |
| GET | `/api/factory/production-profiles` | 规格读取 |
| GET | `/api/factory/workflow` | 工作流读取 |
| GET | `/api/factory/models` | 脱敏模型配置读取 |
| GET | `/api/factory/overview` | 概览；原能力观察可能探测已保存配置 |
| GET | `/api/factory/capabilities` | 原能力观察；不接受任意目标 URL |
| GET | `/api/factory/projects/{pid}` | 项目读取 |
| GET | `/api/factory/projects/{pid}/shots` | 镜头读取 |
| GET | `/api/factory/projects/{pid}/series` | 系列与版本读取 |
| GET | `/api/factory/projects/{pid}/cloud-budget` | 既有费用账本读取 |
| GET | `/api/factory/projects/{pid}/flow` | 团队流程读取 |
| GET | `/api/factory/jobs/{jid}` | 新增只读 SQLite 状态；不触发远程轮询 |
| POST | `/api/factory/jobs/{jid}/pause` | 任务控制；revision + lease 双围栏 |
| POST | `/api/factory/jobs/{jid}/cancel` | 任务控制；不伪造供应商取消能力 |
| POST | `/api/factory/models/test` | 外部网络探测；仅已保存的 profile_id/kind |
| POST | `/api/factory/models` | 配置修改；原配置 CAS 和连接策略 |
| POST | `/api/factory/projects` | 项目修改；原 Schema |
| POST | `/api/factory/projects/{pid}/shots/{shot_id}` | 选片/编辑；原 CAS |
| POST | `/api/factory/projects/{pid}/series` | 系列版本绑定修改 |
| POST | `/api/factory/projects/{pid}/cloud-authorize` | 人工费用授权 |
| POST | `/api/factory/projects/{pid}/cloud-confirm-cost` | 唯一账本费用确认 |
| POST | `/api/factory/projects/{pid}/decision` | 人工决策 |
| POST | `/api/factory/projects/{pid}/action` | 原暂停、恢复、返修 |

Factory POST 限制 64 KiB JSON。没有引入第二套租户、队列、Agent 或认证数据库。

## 请求、响应与媒体

仅接受规范 origin-form 请求目标，最大 4096 字符；拒绝绝对 URL、点段、反斜杠、控制字符、非法 UTF-8、非法或二次百分号编码。Header 名称和值总量限制 32 KiB；拒绝重复 Host、Origin、Content-Length、Content-Type、Range 等敏感字段、obs-fold、控制字符、Transfer-Encoding、Content-Encoding 和 Expect。连接读取空闲超时 5 秒，不把它宣称为完整公网抗慢连接方案。

JSON 必须为唯一成员的对象，数值有限，字符串可编码为 UTF-8，嵌套不超过 64 层。拒绝 NaN/Infinity、浮点溢出、孤立代理字符；深度限制不依赖 LangGraph 可能调整过的解释器递归上限。错误保留旧 UI 的 error 字符串，增加稳定 code 和 HTTP status：

```json
{"error":"Invalid request","code":"invalid_json","status":400}
```

旧业务 404/409 语义保留。协议错误使用 400/403/405/408/413/415；Legacy 未预期异常统一安全 500，仅记录异常类型，不回显私人诊断。响应包括 nosniff、no-referrer、same-origin CORP、DENY frame 和敏感内容 no-store；不添加 CORS 放行。

媒体使用 dir_fd + O_NOFOLLOW 逐组件打开，fstat 确认为普通文件，不先 resolve 再按不可信路径重开。所有符号链接均拒绝，包括指向同项目内部的链接。保留单 Range、后缀 Range、206 和 Content-Range，空文件 Content-Length 为 0。无 POSIX 描述符能力返回 503，不静默降级。可信代码根目录由部署管理员控制；不对恶意本机管理员承诺隔离。

## 任务生命周期契约

快照分别返回 raw queue_state、展示 state、project_state、project_revision、lease、phase、next_poll_at，以及最多 100 条从唯一云账本读出的 task_id/file_id/status；截断显式标记。历史未知字段不推断。控制正文恰好为两个整数 expected_revision 和 expected_lease，在 BEGIN IMMEDIATE 中比较后复用原 store.action 的事务内暂停逻辑。

| 状态 / 请求 | 行为 | 安全边界 |
| --- | --- | --- |
| queued / pause | 202，项目暂停，本地待执行唤醒记录取消 | 不删除远程任务、不释放未知费用 |
| running / pause | 202，pause_requested，等待原安全边界 | 不杀 worker，不声称已经暂停 |
| 重复 pause | 当前围栏匹配时 200 / dispatched=false | 不重复写 revision |
| revision 或 lease 变化 | 409 stale_job | 不操作新一次执行 |
| 本地终态 job | 200 no-op | 不暂停后来启动的任务，不证明供应商已取消 |
| 不存在的 job | 404 job_not_found | 不伪造成功 |
| 未知队列状态 | unknown；控制拒绝 | 不推断 completed |
| 活跃 job / cancel | 409 cancel_not_supported，external_cancelled=false | 不以暂停冒充取消，不发起网络请求 |

旧队列 cancelled 可能仅表示本地唤醒记录取消，所以同时公开项目状态及账本状态。暂停仍是项目级安全暂停，不承诺单独中断某个远程镜头。

## 上游源码、取舍与许可

Comfy-Org/ComfyUI 固定 commit：`12081cf9b8cb2c8128e32f689aabc8ac4688fde7`。该研究版本不改变 DEPLOYMENT 中已验证的部署版本。

| 实际阅读的固定源码 | 采用的工程思想 | 本项目适配 / 未采用原因 |
| --- | --- | --- |
| [server.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/server.py)：create_origin_only_middleware、注册入口 | 路由前统一 Origin / Fetch Metadata 准入 | 标准库 mixin 覆盖新旧入口；不复制非 loopback 放宽，不将 Origin 当认证 |
| 同文件 /api/jobs、单 job/cancel、/prompt、/history | 规范 ID，归一队列/历史，先校验再入队，针对性控制 | 保留 SQLite、原 ID 与账本；不加入 aiohttp/新队列，不把内存中断用于付费远程请求 |
| [comfy_execution/jobs.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/comfy_execution/jobs.py) | cancel_job 按实际 interrupt/dequeue 结果处理 pending→running、running→finished 竞争 | revision/lease + 事务；未知和不支持显式失败，不根据旧快照声称取消 |
| [tests-unit/jobs_cancel_test/jobs_cancel_test.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/tests-unit/jobs_cancel_test/jobs_cancel_test.py) | 定向取消、竞态、终态 no-op、HTTP 契约测试 | 临时 SQLite 验证过期围栏、其他项目不受影响和未知云账本保留 |
| [tests/execution/test_jobs.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/tests/execution/test_jobs.py) | ID 类型与规范形式、状态归一化 | 沿用本项目标识格式，不隐式接受别名 |

已阅读固定版本的 [LICENSE](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/LICENSE)，为 GPL-3.0 文本。本 PR 独立实现通用设计，没有复制上游源码/测试/许可证，没有新增上游运行依赖。研究 clone 不提交 Git。

## HTTP 安全审查表

| 审查项 | 已有证据 / 触发条件 | 处理与验证 |
| --- | --- | --- |
| 继承边界不统一 | 原 Factory _local_request 只守部分新路径，父类 GET 可接受不受信 Host/Origin；隔离 HTTP 测试观察到接受行为 | 统一方法分派前准入；Factory/Legacy 双套真实 loopback HTTP 测试 |
| DNS rebinding 风险 | 完整攻击还依赖浏览器、恶意域名解析及其网络策略；没有在生产利用 | 非允许 Host 403，不查 DNS；Header 测试不等同完整攻击复现 |
| 跨站 / 无 Origin | 原 Legacy 固定 8787 Origin，缺 Origin 时不检查 Host | 实际端口精确同源、Fetch Metadata；本地无 Origin CLI 继续通过 |
| 非法 Header / JSON | 基线重复成员与 framing 等校验不一致 | 重复/折叠/控制字符、大小、深度、UTF-8、有限值、超时与无副作用断言 |
| 媒体路径与链接 | 原 resolve 检查不能证明逐组件不跟随链接 | 描述符级打开；穿越、编码、符号链接与 Range 回归 |
| 外部请求 | providers.py / video_transport.py 已有明确网络策略 | 保留 DNS/peer、TLS、禁重定向、大小/超时、无自动重试；原测试全量回归 |
| 任务控制 | 暂停不等于供应商取消 | can_cancel=false；暂停后账本及其他项目不变 |
| 非 loopback | 仅配置 host 不构成公网安全 | 启动硬门禁；测试确保数据库初始化未发生 |
| 异常响应 | 独立 Legacy 未预期 OSError 曾断开连接 | 4 个参数化测试；修复前 3 fail / 1 pass，修复后统一安全 JSON |
| 浏览器兼容 | 模拟 Node 后端不能证明 Python 边界 | 新增 3 个真实 Python 临时后端浏览器用例，与既有 5 个 UI 用例保留 |

原基线 108 项新增 HTTP 测试为 94 failed / 14 passed；这是临时环境的回归证据，不是生产漏洞利用。原隔离环境最终专项 185 passed、完整 Factory 660 passed（1 条多线程 fork DeprecationWarning 未屏蔽），Node 54 passed。恢复工作区的复验结果及 GitHub CI 以 [PR 报告](NEXT_ROUND_PR_REPORT.md) 为准。

ChatGPT 容器 Chromium 被管理策略以 ERR_BLOCKED_BY_ADMINISTRATOR 拒绝 loopback 导航，8 项 browser 测试未通过，状态 blocked；没有关闭或绕过策略，等待正常 GitHub CI 验证。所有媒体均为 CPU 合成测试图样，不证明 GPU/云模型或艺术质量。

## 部署与回滚

无数据库迁移、模型下载或生产服务操作。管理员部署前需检查 host、隧道/代理端口和 Host/Origin 是否匹配，以及媒体是否依赖符号链接。旧 CLI、工作台路径、Legacy API、模型配置 CAS、项目决策、云账本和恢复状态保持。回滚代码不需要逆向迁移，但会恢复旧 HTTP 边界缺口；即使回滚也不得开放不安全公网访问。本轮不执行现场部署。

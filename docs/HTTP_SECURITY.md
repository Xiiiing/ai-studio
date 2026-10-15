# HTTP 安全边界与任务 API 审查表

审查基线：`4aaece74ab54f38c125c564accc20f4134fb5bab`。工作包 7，分支 `feat/http-security-hardening`。

## 信任模型与范围

本服务是**单用户、loopback 信任域内的制作管理接口，不是公网认证系统**。本机进程仍属于信任域；Host/Origin 校验不能防止具有本机执行权限的攻击者。Factory 和 Legacy 共用 `studio.http_security.LocalHTTPBoundary`，在方法分派之前进行准入，子类转交父类处理不会绕过它。

默认绑定不变。`factory.web.main()` 在初始化数据库和打开监听端口之前拒绝非 loopback 地址，没有 `allow_unsafe` 开关。共同请求入口还验证实际监听地址，防止嵌入式使用 Handler 时绕过启动门禁。`localhost` 被直接归一为 `127.0.0.1`，不进行 DNS 解析。IPv6 `::1` 可以显式配置。Legacy 独立入口仍绑定 `127.0.0.1:8787`。

服务只接受 loopback peer、与实际监听端口匹配的一个 Host，以及缺省或与该 Host 精确同源的一个 HTTP Origin。允许的主机名为 `localhost`、`127.0.0.1`、`[::1]` 和实际绑定的 loopback 字面地址。不接受任意解析到本机的域名、尾点域名、用户信息或代理头代替 Host。`Sec-Fetch-Site: cross-site` / `same-site` 被拒绝；无 Origin 的本地 CLI 请求继续可用。不会信任 `X-Forwarded-*`。

远程使用优先采用保留同一端口的 SSH 隧道，例如本地与服务端都使用 8787。直接监听 LAN/公网地址在本版本中明确不支持。经过认证的反向代理还必须自行实施 TLS、原始 Host/Origin/CSRF 检查、身份验证和访问限制，再连接 loopback 上游；**不能仅删除或改写 Origin 就宣称部署安全**。此类代理部署未在本轮验证。

## 完整路由清单

实现来源：`studio/http_routes.py`。下表中 `pid` 是现有 12 位小写十六进制项目 ID，`jid` 是现有 32 位小写十六进制队列 ID；不是供应商的 task_id。Legacy slug 和镜头 ID 沿用 `studio.core` 的原有正则。不注册未实现的新路由；不支持的方法返回 405，未知路由返回 404。

| 方法 | 路径 | 分级 | 请求体上限 / 业务边界 |
| --- | --- | --- | --- |
| GET | `/` | UI | Factory 工作台或独立 Legacy 主页 |
| GET | `/legacy` | UI | Factory 内的原 Legacy 主页 |
| GET | `/factory/static/*` | UI | 受限静态扩展名、禁止符号链接 |
| GET | `/api/health` | 读取 | Legacy 状态，不再绕过共同准入 |
| GET | `/api/episodes` | 读取 | Legacy 项目清单 |
| GET | `/api/episode/{slug}` | 读取 | Legacy 项目状态 |
| GET | `/api/jobs` | 读取 | 原 Legacy 作业清单，保持语义 |
| GET | `/media/production/*` | 媒体 | 白名单扩展名、描述符级路径检查、单 Range |
| POST | `/api/start` | 任务控制 | 8 KiB；原 Legacy 启动作业语义 |
| GET | `/api/factory/production-profiles` | 读取 | 现有规格 |
| GET | `/api/factory/workflow` | 读取 | 现有工作流定义 |
| GET | `/api/factory/models` | 读取 | 脱敏模型配置 |
| GET | `/api/factory/overview` | 读取 | 原概览；能力观察可能探测已保存配置 |
| GET | `/api/factory/capabilities` | 读取 | 原能力观察；不接受任意目标 URL |
| GET | `/api/factory/projects/{pid}` | 读取 | 项目状态 |
| GET | `/api/factory/projects/{pid}/shots` | 读取 | 逐镜工作台 |
| GET | `/api/factory/projects/{pid}/series` | 读取 | 系列绑定与版本 |
| GET | `/api/factory/projects/{pid}/cloud-budget` | 读取 | 既有费用账本 |
| GET | `/api/factory/projects/{pid}/flow` | 读取 | 团队流程 |
| GET | `/api/factory/jobs/{jid}` | 读取 | 新增，只查 SQLite，不触发远程轮询 |
| POST | `/api/factory/jobs/{jid}/pause` | 任务控制 | 64 KiB；revision + lease 双围栏 |
| POST | `/api/factory/jobs/{jid}/cancel` | 任务控制 | 64 KiB；不伪造供应商取消能力 |
| POST | `/api/factory/models/test` | 网络探测 | 64 KiB；仅已保存的 profile_id/kind |
| POST | `/api/factory/models` | 配置修改 | 64 KiB；原配置 CAS、连接策略校验 |
| POST | `/api/factory/projects` | 修改 | 64 KiB；现有项目 Schema |
| POST | `/api/factory/projects/{pid}/shots/{shot_id}` | 修改 | 64 KiB；保留选片与局部编辑 CAS |
| POST | `/api/factory/projects/{pid}/series` | 修改 | 64 KiB；原版本绑定校验 |
| POST | `/api/factory/projects/{pid}/cloud-authorize` | 费用授权 | 64 KiB；原人工费用审批 |
| POST | `/api/factory/projects/{pid}/cloud-confirm-cost` | 费用确认 | 64 KiB；原唯一账本 |
| POST | `/api/factory/projects/{pid}/decision` | 任务控制 | 64 KiB；原人工决策门禁 |
| POST | `/api/factory/projects/{pid}/action` | 任务控制 | 64 KiB；原暂停、恢复与返修语义 |

所有类别都经过同一个准入边界；分级用于审计，不表示读取或媒体接口可以公开访问。

## 请求、响应和媒体

只接受 origin-form 的规范路径；拒绝点段、反斜杠、控制字符、非法 UTF-8、错误百分号编码及二次编码路径。拒绝重复 Host、Origin、Content-Length、Content-Type、Range 等敏感 Header，以及 obs-fold、控制字符、Transfer-Encoding、Content-Encoding 和 Expect。Header 接受上限 32 KiB，目标长度上限 4096 字符，连接读取的空闲超时为 5 秒；这不是对公网慢速连接攻击的完整防御。

JSON 必须是有限数值、唯一成员、可编码为 UTF-8 的对象；拒绝 NaN、Infinity、溢出的浮点数、孤立代理字符和超过 64 层的结构。大小先于读取正文检查，不依赖 LangGraph 可能调整过的 Python 递归上限。旧业务的 409/404 处理保留；协议错误返回适当的 400/403/405/408/413/415 等状态。错误统一为：

```json
{"error":"供旧 UI 显示的字符串","code":"稳定的机器可读代码","status":400}
```

媒体路径采用 `dir_fd` + `O_NOFOLLOW` 逐组件打开，再通过 `fstat` 确认为普通文件；不会先 resolve 后重新按不可信路径打开。包括指向同一项目内文件的符号链接也拒绝。保留单区间、后缀区间、206 和 Content-Range。空文件的 Content-Length 为 0。缺少 POSIX 描述符能力时返回 503，不静默降级为可跟随符号链接的读取。受信任的代码根目录由部署管理员控制，不将此机制描述为对恶意本机管理员的隔离。

响应带有 nosniff、no-referrer、same-origin CORP、DENY frame，以及敏感内容的 no-store。没有添加 CORS 放行头。

## 任务状态契约

`GET /api/factory/jobs/{jid}` 返回原始 `queue_state`、展示用 `state`、独立 `project_state`、`project_revision`、`lease`、本地 phase、下一次轮询时间，以及最多 100 条原账本远程任务引用；截断通过 `remote_tasks_truncated` 明示。供应商 task_id/file_id/status 仅从既有账本读取，不根据本地状态推断。

控制正文必须恰好包含整数 `expected_revision` 和 `expected_lease`。在 `BEGIN IMMEDIATE` 内核对当前作业和项目，再调用从旧 `store.action(pause)` 原样提取的事务内暂停逻辑；不引入第二套队列或数据库 Schema。

| 情况 | API 行为 | 不会发生的动作 |
| --- | --- | --- |
| queued 作业暂停 | 202；暂停项目并取消其待执行的本地唤醒记录 | 不删除云任务、不释放未知费用、不取消已提交供应商任务 |
| running 作业暂停 | 202；`pause_requested`，等待既有安全边界 | 不杀 worker、不声称已经暂停 |
| 重复暂停 | 当前围栏匹配时 200 / dispatched=false | 不重复更新版本或派发动作 |
| revision/lease 已变化 | 409 / stale_job | 不控制新一次执行 |
| 本地终态作业 | 200 / dispatched=false，仍返回原始状态 | 不暂停后来启动的无关执行 |
| 未知作业 ID | 404 / job_not_found | 不伪造取消成功 |
| 未知队列状态 | 展示 unknown；控制拒绝 | 不猜测为 completed |
| 活跃作业 cancel | 409 / cancel_not_supported | 不用 pause 冒充 cancel，不隐式发起网络请求 |

`cancelled` 在旧本地队列中可能只是唤醒记录被取消，因此总是同时返回项目状态和远程账本状态。项目暂停仍是项目级安全暂停，不承诺只暂停某一个远程镜头。终态响应是 no-op，不是供应商取消的证明。

## 上游源码学习与许可

研究仓库：[Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI)，固定 commit `12081cf9b8cb2c8128e32f689aabc8ac4688fde7`。研究版本不改变本项目已验证的 ComfyUI 部署版本。

| 固定源码 | 实际设计 | 本项目适配 / 未采用的部分 |
| --- | --- | --- |
| [server.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/server.py) 的 `create_origin_only_middleware`、注册入口 | 统一入口在路由前检查 Origin / Fetch Metadata；loopback 才检查 Host 对应关系 | 使用标准库 Handler mixin 覆盖新旧入口；不复制其非 loopback 放宽逻辑，不将 Origin 当身份认证 |
| 同文件 `/api/jobs`、单 job、cancel、`/prompt`、`/history` | 队列/历史视图归一化，规范 ID，先校验后入队，指定任务取消 | 保留 SQLite 与现有 32 位 ID、原账本；不添加 aiohttp/新队列，不把内存中断迁移到付费远程任务 |
| [comfy_execution/jobs.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/comfy_execution/jobs.py) | `cancel_job` 根据 interrupt/dequeue 的真实结果处理 pending→running 和 running→finished 竞争，不靠旧快照宣称成功 | 使用事务 + revision/lease 围栏；未知与不支持的操作显式返回，不能借“取消”重新提交云视频 |
| [tests-unit/jobs_cancel_test/jobs_cancel_test.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/tests-unit/jobs_cancel_test/jobs_cancel_test.py) | 针对性取消、竞态、终态/未知 no-op、HTTP 契约测试 | 增加过期 lease、过期 revision、另一项目不受影响、未知账本结果保留的临时数据库测试 |
| [tests/execution/test_jobs.py](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/tests/execution/test_jobs.py) | UUID 规范形式、类型和任务状态归一化测试 | 沿用本项目标识格式，不接受别名或隐式改写 ID |

已阅读该 commit 的 [LICENSE](https://github.com/Comfy-Org/ComfyUI/blob/12081cf9b8cb2c8128e32f689aabc8ac4688fde7/LICENSE)（GPL-3.0 文本）。本 PR 独立实现通用设计，没有复制上游源码、测试或许可文件，没有新增上游运行依赖。研究用 bare clone 位于被忽略的 runtime 目录，不提交 Git。

## HTTP 安全审查表与证据

| 检查项 | 基线证据 / 触发条件 | 本轮措施与验证 |
| --- | --- | --- |
| Legacy 继承绕过 | Factory 原 `_local_request` 只守部分新路径，父类 GET 仍接受不受信 Host/Origin；临时 HTTP 服务可重现接受行为 | 共同 parse_request 准入；Factory/Legacy 参数化真实 loopback HTTP 测试 |
| DNS rebinding 风险 | 攻击前提包括访问者浏览器、攻击域名解析和浏览器网络策略；未在生产环境执行攻击 | 非允许 Host 直接 403，不执行 DNS；测试是 Header 层证据，不宣称已复现完整攻击链 |
| 跨站与缺 Origin | 原 Legacy POST 使用固定 8787 Origin，缺 Origin 不检查 Host | 同源实际端口 + Fetch Metadata；本地 CLI 无 Origin 仍通过，跨端口 Origin 被拒 |
| 非法 Header / JSON | 基线在重复成员、重复 framing 和结构检查方面不一致 | Header、正文长度、UTF-8、有限数值、深度、超时测试；副作用桩不被调用 |
| 媒体穿越 / 链接 | 原 resolve 后检查路径再打开，不能以此声明逐组件不跟随链接 | dot/编码/符号链接测试，描述符读取与 Range 回归 |
| 外部请求 | `providers.py` 与 `video_transport.py` 已有明确网络策略和固定路由；未发现需要在本工作包重写的证据 | 保留 DNS/peer、TLS、禁止重定向、大小/超时、无自动重试等原机制；原模型接口/传输回归一起运行 |
| 任务控制 | 原项目暂停不能被描述为供应商取消 | 新 job 契约显式 can_cancel=false；临时 SQLite 验证账本与另一项目不变 |
| 非 loopback 部署 | 只配置 host 不能构成公网安全方案 | 启动前硬门禁；测试断言数据库初始化未发生 |
| 浏览器与旧界面 | 单纯 Node 模拟后端不能证明 Python HTTP 边界兼容浏览器 | 增加真实 Python 临时后端的浏览器用例；生成仅 CPU 测试图样，无模型调用 |

基线 108 项新 HTTP 测试：94 failed / 14 passed；这些失败是隔离回归证据，不是生产漏洞利用记录。继续审查发现嵌入式非 loopback 监听未被请求入口拒绝，新增 3 项测试在修复前全部失败。2026-10-14 本轮复测：HTTP/模型接口/job 专项 188 passed；完整 `tests/test_factory_*.py` 为 663 passed，保留 1 条多线程 fork DeprecationWarning；Node 54 passed；真实浏览器 8 passed，0 skipped。

早期容器的 `ERR_BLOCKED_BY_ADMINISTRATOR` 结果保留为环境阻断证据，没有关闭该策略。本轮改用已有的固定摘要 Playwright 隔离镜像，禁用外网、不挂载 GPU 或生产数据库，完整执行原 `npm run test:browser`。首次实际浏览器运行还暴露了测试观察方式错误：CORP 使跨源响应表现为 `ERR_BLOCKED_BY_RESPONSE.NotSameOrigin`，无法等待普通 response 事件。测试改为同时断言浏览器 blocked、真实 Python 服务端返回 403、launch 调用计数为 0；没有放宽状态码或删除断言。测试夹具的并发 JSON 行输出也加锁，避免证据相互串行污染。早期失败日志和 trace 均保留在隔离工作树的 `runtime/audit-evidence/`。实时 CI 状态见本轮 PR 报告。

## 部署与回滚

没有数据库迁移、模型下载、服务重启或生产配置写入。部署前先核查 host 为 loopback、媒体访问不依赖符号链接、代理/隧道端口与 Host/Origin 契约匹配。由管理员按既有升级流程安排部署；本轮未执行这些操作。旧 CLI、工作台路径、Legacy API、模型配置 CAS 和项目决策结构不变。回滚代码无需逆向迁移，但会重新暴露旧 HTTP 边界缺口；仍必须限制为 loopback，不能因回滚恢复不安全公网部署。

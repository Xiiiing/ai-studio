# 模型连接重构：依据与验收

## 对比的项目

2026-10-17 对比以下项目的官方文档和界面源码，未运行其完整应用或供应商生产调用，也未复制其实现代码。

| 项目 | 核对的官方材料 | 采用的做法 |
| --- | --- | --- |
| Cherry Studio | [服务商配置说明](https://www.cherryai.com/docs/en/pre-basic/providers/)、[ApiKey](https://github.com/CherryHQ/cherry-studio/blob/main/src/renderer/pages/settings/ProviderSettings/ConnectionSettings/ApiKey.tsx)、[ModelList](https://github.com/CherryHQ/cherry-studio/blob/main/src/renderer/pages/settings/ProviderSettings/ModelList/ModelList.tsx) | 服务商凭据和该服务的模型目录分别管理；读取目录后选择模型，保留手动添加 |
| Open WebUI | [Connect a Provider](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/)、[AddConnectionModal](https://github.com/open-webui/open-webui/blob/main/src/lib/components/AddConnectionModal.svelte)、[Connections](https://github.com/open-webui/open-webui/blob/main/src/lib/components/admin/Settings/Connections.svelte) | 连接以 URL、凭据和实际协议为中心，连接与日常选择模型分开 |
| LobeHub | [ProviderConfig](https://github.com/lobehub/lobehub/blob/canary/src/features/Settings/provider/features/ProviderConfig/index.tsx)、[CustomProviderDetail](https://github.com/lobehub/lobehub/blob/canary/src/features/Settings/provider/detail/default/CustomProviderDetail.tsx) | 官方服务预设地址，Key 为主要输入；自定义服务与默认服务共用连接能力，专用参数逐层展开 |
| AnythingLLM | [LLM configuration](https://docs.anythingllm.com/setup/llm-configuration/overview)、[OpenAiOptions](https://github.com/Mintplex-Labs/anything-llm/blob/master/frontend/src/components/LLMSelection/OpenAiOptions/index.jsx)、[GenericOpenAiOptions](https://github.com/Mintplex-Labs/anything-llm/blob/master/frontend/src/components/LLMSelection/GenericOpenAiOptions/index.jsx) | 常用凭据和模型选择保持简短；内置服务与兼容端点使用对应的配置控件 |

这些项目支持的协议与功能不同。本项目继续使用已经实现的 Chat Completions、Anthropic Messages、Gemini generateContent、Ollama 原生适配器；未因对比其他项目而宣称支持 OpenAI Responses 或任意媒体 API。

## 最终交互

- **服务连接**：桌面左侧服务列表、右侧服务与模型；手机先显示服务列表，点入服务查看详情并可返回。搜索覆盖服务商、端点和模型。每个服务显示真实协议和官方文档入口。
- **添加服务**：先选带官方资源标识的服务商。内置云服务主要填 Key；本地／自定义服务填 URL 和可选 Key。地址覆盖、服务端变量、网络规则和超时位于高级连接设置。
- **选择模型**：预设模型可直接使用；“获取模型”读取当前 URL 和凭据的实际目录，勾选多个模型。目录失败仍可手动添加。视觉能力仅来自已明确设置的预设、目录声明或用户设置，不根据模型名字猜测。
- **保存连接**：一次保存提交整个服务及其模型，成功后关闭表单；失败保留输入。移除“加入草稿→页面再次保存”的流程。界面不显示或要求用户编写内部配置编号。
- **制作分工**：文本创作、文本审稿、视觉审核三个常用选择；图像、视频、配音分别选择服务。已有不同角色分工显示“已按角色分别配置”，不擅自统一。展开后仍可逐角色分配或关闭总制片代理。分工单独保存，尚未保存时不混入连接编辑。
- **高级工具**：独立的 ComfyUI API 工作流导入、导出；不会隐式保存模型配置或启动生成。

## 配置兼容与调用

服务分组由实际 provider、完整端点、鉴权引用、TLS 身份及访问范围确定；不同凭据、网关前缀或客户端证书的连接不会自动合并。执行仍使用原有模型 profile ID，角色绑定及运行任务快照保持兼容。模型各自保留上下文、视觉能力、结构化输出和推理参数。

同一保存请求中分配同一个新 Key 的模型共用一个新随机引用。不会扫描旧密钥库来猜测连接归属；旧引用继续供运行任务使用。编辑已有服务时可不重新输入 Key，轮换时更新该服务的全部模型。Key 只存在于写入请求及服务端私有文件，不返回浏览器或进入浏览器持久化。

H3、ComfyUI、百炼媒体和本机 Qwen3-TTS 继续使用各自已实现的生命周期。未把制作服务器的进程或工作流伪装成开发机 API。未修改的历史 H3 预览保留缓存身份，主动切换质量档才使用对应采样配方。

每次连接或分工保存带打开时的配置版本；冲突保留表单，不自动覆盖其他终端的新配置。保存、目录请求和检查有进行中状态并阻止重复提交；目录结果与连接修改关联，旧结果不会冒充当前连接状态。

## 验证范围

验证覆盖隔离 Python 服务与本地 HTTP 原生协议目录、私有 Key 保存和多模型共享引用；前端覆盖保存冲突、稳定角色绑定、mTLS、跨页面分工草稿和检测结果过期。浏览器在桌面与手机上实际完成云服务 Key、自定义 URL 目录勾选、多模型保存、密钥轮换、制作分工、H3 采样设置及工作流导入，检查操作按钮可达与横向溢出，并执行全工作台回归。

浏览器使用明确标识的模拟服务及假 Key；未调用供应商生成、未重试制作项目。部署和真实模型效果验收另行安排。截图见 [模型服务截图目录](images/model-services/)。品牌资源的官方来源与哈希见 [PROVIDER_BRAND_ASSETS.md](PROVIDER_BRAND_ASSETS.md)。

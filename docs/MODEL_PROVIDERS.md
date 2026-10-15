# 模型接口、角色分工与视频服务

协议核实日期：**2026-10-13**。本页说明当前代码已经实现的连接方式、配置生效范围和恢复行为。验证依据是 CPU 测试、mock 和测试自己的 loopback HTTP 服务；没有把这些测试当作真实云 API、GPU 推理或生成质量的验收。

## 1. 角色、配置档与服务

- **角色**决定本次工作，例如编剧、独立审稿、分镜导演、视觉质检。
- **模型配置档**保存 provider、模型 ID、完整连接前缀、能力声明、上下文和生成参数。
- **服务**加载权重并执行推理，可以在本机、局域网机器或云端运行。

文本角色通过 `llm_roles` 引用 `llm_profiles`；视频角色通过 `video_roles.video_generator` 引用 `video_profiles`。多个角色可以共享模型，也可以让编剧、审稿和视觉质检分别使用不同模型。更换角色提示词不代表更换了模型。

工作流由 LangGraph 编排器调用角色。可选的 executive_producer 总制片代理只在合格候选中选择故事与视觉，不创建第二套调度器，也不授权新增费用或批准终片。新项目默认代管；该角色未配置时明确回到人工选择。工作区展示当前分配、真实任务、交接和历史回执；历史模型、任务 ID 和时间来自实际记录，不以当前配置补写。

`factory.llm.json_call` 使用文本适配器；`factory.video_providers` 解析视频分工；MiniMax 的提交、轮询和下载由独立的视频协议处理。模型设置页面不会安装权重、启动文本模型服务或执行任意 shell 命令。

## 2. 文本模型支持矩阵

下表中的基址是配置示例，不会自动探测、替换或创建对应服务。DeepSeek、Qwen 等模型既可以由本地运行时部署，也可以使用云 API；选择 provider 时应匹配实际提供的 HTTP 协议。

| provider | 使用的协议 | 完整基址示例 | 默认输出约束 | 默认模型目录检查 |
| --- | --- | --- | --- | --- |
| `llama_cpp` | OpenAI Chat + llama.cpp 模板/tokenizer | `http://127.0.0.1:8090/v1` | `llama_json_schema` | `required` |
| `ollama` | Ollama 的 OpenAI 兼容接口 | `http://127.0.0.1:11434/v1` | `openai_json_schema` | `required` |
| `ollama_native` | Ollama 原生 Chat / Tags | `http://127.0.0.1:11434` | `openai_json_schema`，转换为原生 schema | `required` |
| `vllm` | OpenAI 兼容接口 | `http://127.0.0.1:8000/v1` | `openai_json_schema` | `required` |
| `lm_studio` | LM Studio 的 OpenAI 兼容接口 | `http://127.0.0.1:1234/v1` | `openai_json_schema` | `required` |
| `deepseek` | DeepSeek OpenAI 兼容接口 | `https://api.deepseek.com` | `json_object` | `required` |
| `dashscope` | Qwen / 百炼 OpenAI 兼容接口 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `json_object` | `skip` |
| `openai_compatible` | 用户配置的 Chat Completions 网关 | `https://api.example.com/custom-prefix` | `json_object` | `required` |

`llama_cpp` 文本调用使用服务端模板和 tokenizer 检查上下文；带图像的调用及其他 provider 使用保守的 UTF-8 字节、消息开销和图像预算估算。模型仍须实际支持所选输出格式和视觉能力。

### 完整 API 前缀

除原生 Ollama 的明确路由规则外，文本请求在 `base_url` 后追加 `/chat/completions`，目录请求追加 `/models`。**应用不会统一追加 `/v1`。**

| 配置的 `base_url` | 实际 Chat 路径 |
| --- | --- |
| `https://api.deepseek.com` | `https://api.deepseek.com/chat/completions` |
| `http://127.0.0.1:1234/v1` | `http://127.0.0.1:1234/v1/chat/completions` |
| `https://workspace-id.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` | 相同前缀后追加 `/chat/completions` |
| `https://gateway.example.com/custom-prefix` | `https://gateway.example.com/custom-prefix/chat/completions` |

百炼示例中的 `workspace-id` 必须替换为真实工作空间域名，并同步修改 `allowed_hosts`。地域、工作空间地址和密钥应以当前服务控制台为准。不要把完整的 `/chat/completions` 端点填进 `base_url`。

## 3. 模型目录检查与“不生成内容”的预检

`model_discovery` 决定是否要求服务提供当前前缀下的模型目录：

| 配置 | 文本预检行为 | 预检结果含义 |
| --- | --- | --- |
| `required` | OpenAI 兼容接口请求 `GET /models`；原生 Ollama 请求 Tags | `ready` 仅表示目录请求成功且配置模型或显式别名匹配，不代表已验证生成或 schema 能力 |
| `skip` | 只校验本地配置，不发网络请求 | `unverified`、`model_match: false`；连接在线状态和模型身份尚未验证 |
| 配置已禁用 | 不发网络请求 | `disabled` |
| 目录、鉴权或匹配检查失败 | 不调用生成接口 | `error`，附稳定错误代码 |

百炼 Chat 与模型目录可能使用不同前缀、接口和响应包装。当前适配器不会推断另一套目录地址，因此 `dashscope` 默认 `skip`。只提供 Chat 的其他兼容网关也可以显式选择 `skip`；`llama_cpp` 和 `ollama_native` 必须保留 `required`。

跳过目录不跳过完成响应的身份检查：每次文本生成仍要求响应 `model` 匹配配置 ID 或 `model_aliases`。不完整输出、未知模型和 HTTP 错误不会触发切换模型或切换协议。

视频预检同样不提交生成任务，也不发网络请求。它检查配置和密钥环境变量是否存在，返回 `configured` / `disabled` / `error`，并明确 `network_checked: false`、`model_match: false`。保存配置和这两类预检都不会发起收费的文本或视频生成。

## 4. Ollama 原生接口与本地部署

先在部署环境准备模型并启动服务，例如：

```bash
ollama serve
# 在另一个终端安装准备使用的模型
ollama pull qwen3:8b
```

在模型设置里选择 `ollama_native`。以下对象可作为 `llm_profiles` 中的一个配置档；绑定角色时保留其他角色和视觉质检的有效配置：

```json
{
  "provider": "ollama_native",
  "enabled": true,
  "network_mode": "loopback",
  "base_url": "http://127.0.0.1:11434",
  "model": "qwen3:8b",
  "context_tokens": 32768,
  "vision": false,
  "response_format": "openai_json_schema",
  "model_discovery": "required",
  "request_timeout_seconds": 480
}
```

使用已安装的实际模型 tag。Ollama 的省略 tag 与 `:latest` 按同一个名称匹配；其他 tag、仓库命名空间和显式别名仍严格区分。例如 `team/qwen` 可匹配 `team/qwen:latest`，不能匹配 `qwen:latest` 或 `team/qwen:7b`。

原生基址接受服务根和明确的 `/api` 前缀；反向代理子路径会保留：

| 原生基址 | 模型目录 | Chat |
| --- | --- | --- |
| `http://127.0.0.1:11434` | `/api/tags` | `/api/chat` |
| `http://127.0.0.1:11434/api` | `/api/tags` | `/api/chat` |
| `http://127.0.0.1:11434/gateway` | `/gateway/api/tags` | `/gateway/api/chat` |
| `http://127.0.0.1:11434/gateway/api` | `/gateway/api/tags` | `/gateway/api/chat` |

选择 `ollama` 则继续走原有 `/v1` 兼容接口。不要把这两种 provider 的基址和路由混用。

原生协议参数转换如下：

| 项目设置或调用参数 | 发送给 Ollama 的字段 |
| --- | --- |
| 本次输出 `max_tokens` | `options.num_predict` |
| `context_tokens` | `options.num_ctx`；配置大小仍须符合模型和部署资源 |
| 温度、`top_p` | `options.temperature`、`options.top_p`；关闭采样参数发送时省略 |
| 两种 schema 输出模式 | `format: <JSON Schema 对象>` |
| `json_object` | `format: "json"` |
| `prompt` | 不发送 `format`，将 schema 写入角色规则 |
| 可选 `thinking: true/false` | `think: true/false`；省略时保留模型默认行为 |
| 已编码的审核图像 | 消息中的 `images` 数组，仅接受内联 Base64 图像，不额外抓取 URL 或读取指定路径 |

响应中的 `message`、完成原因、token 使用量和耗时转换为统一回执字段；原生响应也保存在对应调用目录。只有完整结束且通过模型身份和本地 JSON Schema 校验的输出才会被采用。

### llama.cpp、vLLM 与 LM Studio

llama.cpp 的两个独立服务示例：

```bash
/path/to/llama-server \
  --model /path/to/writer.gguf --alias drama-writer \
  --host 127.0.0.1 --port 8090 \
  --ctx-size 32768 --parallel 1 --gpu-layers 99
```

```bash
/path/to/llama-server \
  --model /path/to/critic.gguf --alias drama-critic \
  --host 127.0.0.1 --port 8092 \
  --ctx-size 32768 --parallel 1 --gpu-layers 99
```

可通过每个进程的 `CUDA_VISIBLE_DEVICES` 安排 GPU。示例不会自动判断显存容量；视觉模型需要相应服务支持，并按模型要求配置多模态投影权重。将 `vision` 设为 true 不会给文本模型增加视觉能力。

vLLM 服务示例：

```bash
vllm serve /path/to/hf-model \
  --served-model-name drama-local-vllm \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 32768
```

LM Studio 使用其已启动的 OpenAI 兼容服务和服务返回的实际模型 ID。上述服务启动、权重路径、GPU 兼容性和上下文容量由部署者管理。

## 5. 文本参数、思考模式与回执

| 字段 | 行为 |
| --- | --- |
| `model_aliases` | 明确允许响应的模型 ID；不按品牌、前缀或相似名称猜测 |
| `revision` | 用户管理的权重修订标记 |
| `response_format` | `llama_json_schema`、`openai_json_schema`、`json_object`、`prompt` |
| `context_validation` | `llama_tokenize` 或 `conservative_estimate` |
| `max_tokens_field` | 兼容 Chat 请求使用 `max_tokens` 或 `max_completion_tokens`；原生 Ollama 使用 `options.num_predict` |
| `send_sampling_parameters` | 对不接收温度等字段的兼容服务可显式设为 false |
| `temperature_override`、`top_p` | 当前配置档的采样策略 |
| `chat_template_kwargs` | 支持该字段的服务所使用的模板参数 |
| `thinking` | 当前仅支持 `ollama_native` 和 `dashscope` 的布尔值 |
| `model_discovery` | `required` 或 `skip`，语义见上节 |
| `max_schema_attempts` | 包含首次请求在内最多 2 次 schema 尝试，即至多 1 次结构修复 |
| `image_token_budget` | 保守估算路线的每张图像预留量，默认 4096 |

`dashscope` 默认将 `thinking: false` 映射为顶层 `enable_thinking: false`；原生 Ollama 映射为 `think`。是否接受思考选项，以及是否支持所选模型的非流式输出，仍取决于服务。当前文本调用统一为 `stream: false`，没有自动切换到流式协议。

这两个 provider 不接受非空 `chat_template_kwargs`，应使用独立的 `thinking`。其他 provider 不接受该独立字段，应按它们现有的参数能力配置。切换服务类型时需要检查旧高级参数是否仍适用。

任何输出模式都保留本地 JSON Schema 校验；模型身份不匹配、不完整结束、超时或上下文不足会明确失败。schema 修复仅在同一配置档、同一模型上进行。角色回执记录配置档、配置模型、响应模型、实际 API 路由、配置 revision、上下文检查、参数、耗时、使用量和结构修复过程。未记录的使用量不会被估造。

## 6. 局域网、远程服务与凭据

- 本机文本服务使用 `network_mode: loopback`。
- 局域网服务使用 `lan` 并填写精确 `allowed_hosts`；解析结果必须在配置允许的局域网范围内。
- 公网服务使用 `remote`，要求 HTTPS 和精确主机白名单，不支持通配符。
- 带鉴权的非 loopback 连接必须使用 HTTPS；请求不跟随重定向，不借助环境代理改变目的地。
- `base_url` 不接受用户名密码、查询参数或 fragment。
- 配置只保存 `api_key_env` 等环境变量名称；不接受内联 `api_key`、`Authorization` 或任意请求头。

把实际密钥提供给运行 web 和 worker 的环境，例如分别使用 `DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY`、`MINIMAX_API_KEY`。页面无法替 worker 设置进程环境。systemd 部署可使用其环境文件；终端部署需要在启动服务的环境中设置同名变量。

启用远程模型后，对应任务的文本或图像会发送到所选服务。实际密钥值不写入请求文件、错误详情或回执；日志和导出保留的是配置名称和允许公开的执行信息。

## 7. 视频角色与 MiniMax 配置

视频目前有两个 provider：

| provider | 实际执行方式 | 配置范围 |
| --- | --- | --- |
| `comfyui_wan` | 既有 ComfyUI / Wan2.2-TI2V-5B 工作流 | 保留本机进程、GPU 租约和已有质量检查 |
| `minimax` | MiniMax v1 的 image-to-video 提交、状态查询、文件取回 | 独立配置模型、鉴权、时长、分辨率、轮询和下载限额 |

以下是视频配置部分，可与现有文本配置一起保存。示例保留本地默认，云连接初始禁用：

```json
{
  "video_profiles": {
    "local_wan": {
      "provider": "comfyui_wan",
      "enabled": true,
      "model": "Wan2.2-TI2V-5B"
    },
    "minimax_cloud": {
      "provider": "minimax",
      "enabled": false,
      "model": "MiniMax-Hailuo-2.3",
      "base_url": "https://api.minimax.io/v1",
      "network_mode": "remote",
      "allowed_hosts": ["api.minimax.io"],
      "api_key_env": "MINIMAX_API_KEY",
      "download_hosts": [],
      "duration": 6,
      "resolution": "1080P",
      "prompt_optimizer": false,
      "request_timeout_seconds": 60,
      "poll_interval_seconds": 5,
      "max_wait_seconds": 1800,
      "max_download_bytes": 268435456
    }
  },
  "video_roles": {
    "video_generator": "local_wan"
  }
}
```

完成凭据和参数配置后，将云 profile 的 `enabled` 改为 true，再将 `video_roles.video_generator` 设为 `minimax_cloud`。角色不能指向禁用或不存在的配置档。只保存配置不会提交视频任务。

### 生成参数与时间线适配

对于当前示例 `MiniMax-Hailuo-2.3` / `MiniMax-Hailuo-2.3-Fast`，支持 6 秒的 768P 或 1080P，10 秒使用 768P；512P 不适用于这两个模型。已知不合法的 Hailuo 2.3 / 512P 和 1080P / 10 秒组合会在配置阶段被拒绝。其他模型仍须满足该 v1 image-to-video 接口自己的参数矩阵。

当前流程将已批准的镜头关键帧作为首帧。应用限制为 JPEG、PNG 或 WebP，最多 5 MiB，短边大于 300 px，纵横比在 2:5 到 5:2 之间；5 MiB 是本项目为了控制内联请求体而设置的上限。

生成服务按配置的 6 秒或 10 秒输出。项目镜头可更短：下载后保留原始文件，再转换为项目分辨率、24 fps、方形像素和 H.264/yuv420p，并按镜头所需帧数裁短。缩放后按画面中心裁切，不直接拉伸画面。长于配置时长的镜头会在提交前被拒绝，需要调整镜头或选择合法的更长时长。

归一化视频移除服务返回的音轨，交给既有配音、混音、字幕与剪辑链路。成片仍需经过既有技术检查与视觉语义检查。原始视频、归一化结果、校验和及 provider 回执分别保留。

`motion_comic` 使用关键帧和剪辑器运镜；设置视频模型不意味着该格式一定调用视频生成服务。云视频提交与轮询不持有本地视频 GPU 租约；若质量修复需要重新生成关键帧，该图像步骤仍可能使用本地 GPU。

## 8. MiniMax 任务持久化与恢复

当前 MiniMax 适配器只使用以下 v1 操作：

| 操作 | 请求 |
| --- | --- |
| 提交首帧生成任务 | `POST <base_url>/video_generation` |
| 查询同一个任务 | `GET <base_url>/query/video_generation?task_id=...` |
| 获取输出文件信息 | `GET <base_url>/files/retrieve?file_id=...` |
| 下载产物 | 请求鉴权响应给出的下载 URL；不转发 API key |

每个 take 有独立的 `remote_task.json` 和进程间排他锁。应用在唯一一次提交前写入并同步提交意图，收到 task ID 后继续落盘。并发处理同一个 take 的调用返回 `video_task_busy`，不会再发送一次提交。

| 已记录的情况 | 继续运行时的行为 |
| --- | --- |
| 已有 task ID，仍在队列或处理中 | 查询原 task ID；轮询超时会保留它 |
| 服务已经生成，已有 file ID | 重新取回同一个文件的下载信息，不重新生成 |
| 下载超时、临时 HTTP 错误 | 保留 task/file ID，移除未完成的 `.part`，以后继续下载 |
| 文件已下载且校验和匹配 | 复用原始文件；本地转码失败后也可继续处理该文件 |
| 已下载文件校验和不匹配 | 阻断缓存复用，等待处理 |
| POST 结果未知，没有 task ID | 标记 `video_submission_unknown`；继续操作也不会自动再次 POST |
| 服务明确拒绝提交 | 保存 rejected 状态，需要检查配置；同一个 take 不自动重发 |
| 服务明确报告 Fail | 将该 take 标记为失败；后续是否创建新 take 受既有质量与重试预算控制 |
| 输入、首帧或模型设置与未解决任务不一致 | 阻断新请求，先核对原任务，避免把可能仍在计费的任务隐藏掉 |

没有收到 task ID 的未知提交需要人工通过服务端任务记录核对。应用不会猜测 task ID，也没有用“重新提交”冒充恢复；当前未提供云端任务自动撤销协议。恢复已知任务沿用其 journal 中的原始连接配置，继续从对应环境变量读取凭据值。

默认单个 HTTP 请求上限 60 秒，轮询间隔 5 秒，一次等待上限 1800 秒；这些是配置值，不是云服务完成时间保证。主工作流剩余预算也会限制本次等待。应用暂停会保留 journal，已被云端接受的任务可能继续执行。

### 下载地址与凭据隔离

`download_hosts` 可以留空，以接受经过鉴权的文件响应返回的动态 CDN 地址。对于 `network_mode: remote`，下载仍必须使用公网 HTTPS；DNS/IP 验证会拒绝 loopback、局域网和元数据地址。填写列表时仅允许其中的精确主机名。

CDN 下载不携带 MiniMax 的 Authorization、Cookie 或环境代理配置，不跟随重定向。签名下载 URL 不进入 journal 或公开回执。默认最多下载 256 MiB；即使服务未提供 `Content-Length`，读取过程仍执行体积上限和绝对超时检查。

## 9. 配置生效、缓存与现有媒体引擎

文本和视频配置一起使用完整配置 revision、进程锁和原子替换。两个页面同时编辑时，旧 revision 保存会产生冲突；无效的视频配置也不会导致同一次保存中的文本修改部分生效。只更新文本的兼容调用保留现有视频配置。

一次 worker `advance` 可连续执行多个自动节点，直到人工决策、暂停或结束。这一批持有同一份配置快照；下一次队列任务读取更新后的全局配置。未解决的云视频任务还有自己的持久化连接与输入身份，不能通过改配置绕过恢复检查。

文本身份包含 provider、模型、相关行为参数、`thinking` 和 `model_discovery`，用于缓存区分。已有图像、音频和视频是否复用，由实际生成输入、模型设置、质量规则及文件校验和决定，不因为一个角色名称变化就全部重做。

图像仍使用 ComfyUI 中的 FLUX 工作流；配音仍是专用环境里的 Qwen3-TTS 受控进程。它们未被改造成通用聊天请求。新增其他视频、图像或语音引擎，需要分别实现提交、状态、输出、超时、恢复和凭据处理。

## 10. 当前范围与官方协议来源

八个文本 provider 表示明确支持的协议或兼容配置，并不保证所有版本、所有模型都实现相同的参数。当前没有原生 Anthropic Messages、Gemini 或任意 OpenAI Responses 的角色调用实现；用户管理的兼容网关须实际提供这里使用的 Chat Completions 合约。

MiniMax 支持范围是上述 `/v1/video_generation` 生命周期及它支持的模型。**MiniMax H3 的其他协议、其他供应商的视频接口和任意插件接口不会仅凭更换模型名自动适配。** 新模型能否使用，还要核对其 API、分辨率、时长和首帧要求。

以下为本次协议核实所用的官方资料，核实日期为 2026-10-13：

- [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Ollama 原生 Chat](https://docs.ollama.com/api/chat) 与 [Tags 模型列表](https://docs.ollama.com/api/tags)
- [vLLM 在线服务](https://docs.vllm.ai/en/latest/serving/online_serving/) 与 [结构化输出](https://docs.vllm.ai/en/latest/features/structured_outputs/)
- [LM Studio OpenAI compatibility](https://lmstudio.ai/docs/developer/openai-compat)
- [DeepSeek JSON Output 与完整基址示例](https://api-docs.deepseek.com/guides/json_mode/)
- [百炼 OpenAI 兼容 Chat](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope) 与 [模型目录](https://help.aliyun.com/zh/model-studio/list-models)
- [MiniMax image-to-video 参数](https://platform.minimax.io/docs/api-reference/video-generation-i2v)、[任务查询](https://platform.minimax.io/docs/api-reference/video-generation-query)、[文件取回](https://platform.minimax.io/docs/api-reference/file-management-retrieve)

仓库中的 `tests/test_factory_text_adapters.py`、`tests/test_factory_providers.py`、`tests/test_factory_role_routing.py` 和 `tests/test_factory_video_providers.py` 验证协议、身份、网络限制、错误和恢复行为。它们不验证云账户权限、实际部署在线状态、显存容量、生成成本或创作质量；这些需要在目标部署上单独验收。

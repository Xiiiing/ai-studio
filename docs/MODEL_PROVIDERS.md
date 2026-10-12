# 模型接口与本地运行

## 1. 三个概念分开

- **角色**决定本次工作，例如编剧、审稿、分镜导演、视觉质检。
- **模型配置档**决定模型 ID、连接地址、能力、上下文与生成参数。
- **服务**实际加载权重并执行推理，可以运行在本机、另一台局域网机器或远程服务中。

角色通过 `llm_roles` 引用 `llm_profiles`。多个角色可使用同一模型；需要独立模型审核时，将编剧和审稿绑定到不同的服务端权重。不同角色提示词不等于不同模型。

主程序通过 `factory.llm.json_call` 发起调用；`factory.providers` 负责协议、地址、模型身份和配置目录。模型下载、GPU 放置和服务启动仍由部署环境负责，模型设置页面不执行 shell 命令。

## 2. 支持的接口

| provider | 默认基址示例 | 默认输出约束 | 上下文检查 |
| --- | --- | --- | --- |
| `llama_cpp` | `http://127.0.0.1:8090/v1` | `llama_json_schema` | 服务端模板与 tokenizer |
| `ollama` | `http://127.0.0.1:11434/v1` | `openai_json_schema` | 保守估算 |
| `vllm` | `http://127.0.0.1:8000/v1` | `openai_json_schema` | 保守估算 |
| `openai_compatible` | `https://api.example.com/v1` | `json_object` | 保守估算 |

以上适配器使用模型列表和 Chat Completions 协议。它们不是对所有版本、所有模型的功能保证；应为实际服务配置正确的输出形式，并用工作流所需的 schema 验证。Ollama 使用其 OpenAI 兼容接口，并非原生 `/api/chat`。

官方协议说明：
[llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)、
[Ollama](https://docs.ollama.com/api/openai-compatibility)、
[vLLM](https://docs.vllm.ai/en/latest/serving/online_serving/)。

## 3. 本地 llama.cpp

使用与你的 GPU、模型和量化格式兼容的 llama.cpp 构建。准备权重后，分别启动编剧与审稿服务，例如：

```bash
/path/to/llama-server \
  --model /path/to/writer.gguf \
  --alias drama-writer \
  --host 127.0.0.1 --port 8090 \
  --ctx-size 32768 --parallel 1 --gpu-layers 99
```

```bash
/path/to/llama-server \
  --model /path/to/critic.gguf \
  --alias drama-critic \
  --host 127.0.0.1 --port 8092 \
  --ctx-size 32768 --parallel 1 --gpu-layers 99
```

GPU 可通过每个进程的 `CUDA_VISIBLE_DEVICES` 分开安排。以上示例不自动决定使用哪张卡，也不保证给定权重在显存中可容纳。

视觉质检需要实际支持图像的模型和服务。若模型要求多模态投影权重，应按该模型与 llama.cpp 文档配置对应 `--mmproj`；仅把 `vision` 设为 true 不会给文本模型增加视觉能力。

示例配置使用 `drama-writer` 和 `drama-critic` 别名。实际模型列表必须能匹配这些 ID；也可以改配置为服务端已有名称。

## 4. Ollama 与 vLLM

Ollama：

```bash
ollama serve
# 另一个终端
ollama pull qwen3:8b
```

在模型设置中选择 `ollama`，基址填 `http://127.0.0.1:11434/v1`，模型填已安装的实际 tag。配置档里的 `context_tokens` 是本项目的输入上限，仍需让服务本身提供相应上下文。Ollama 的上下文设置方法见[官方兼容说明](https://docs.ollama.com/api/openai-compatibility)。

vLLM：

```bash
vllm serve /path/to/hf-model \
  --served-model-name drama-local-vllm \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 32768
```

模型、运行时和 GPU 必须兼容。vLLM 使用 `openai_json_schema`；本项目不发送已移除的旧 `guided_json` 字段。参见[当前结构化输出文档](https://docs.vllm.ai/en/latest/features/structured_outputs/)。

## 5. 远程与局域网接口

远程示例：

```json
{
  "provider": "openai_compatible",
  "enabled": true,
  "network_mode": "remote",
  "allowed_hosts": ["api.example.com"],
  "base_url": "https://api.example.com/v1",
  "model": "your-served-model-id",
  "api_key_env": "MY_MODEL_API_KEY",
  "context_tokens": 32768,
  "vision": false,
  "response_format": "json_object",
  "request_timeout_seconds": 180
}
```

这是配置格式示例，`api.example.com` 是占位地址。公开配置中的远程示例默认禁用。

- 本机服务使用 `network_mode: loopback`。
- 局域网服务使用 `lan`，显式填写 `allowed_hosts`；地址必须解析到允许的局域网范围。
- 远程服务使用 `remote`，要求 HTTPS 和明确的主机白名单。
- 带鉴权的非本机连接要求 HTTPS；不跟随重定向。
- 密钥只存入运行 web/worker 的环境变量。配置不接受 `api_key`、`Authorization` 或任意请求头。
- systemd 可读取 `config/runtime.env`；普通终端启动时需在该终端环境中设置同名变量。

用户选择启用外部模型后，相关剧本或图像会发送给该服务。连接测试仅调用模型列表接口；保存配置本身不调用生成模型。

原生 Anthropic Messages、Gemini 原生协议及任意云视频协议不在当前适配器内。可以通过用户自己管理的 OpenAI-compatible 网关连接，但仍需验证具体模型与 schema；不把网关名字当成兼容性证明。

## 6. 参数、身份与回执

常用高级字段：

| 字段 | 用途 |
| --- | --- |
| `model_aliases` | 明确允许服务响应的实际模型 ID；不作模糊匹配 |
| `revision` | 自己管理的权重版本或模型修订标记 |
| `response_format` | `llama_json_schema`、`openai_json_schema`、`json_object`、`prompt` |
| `context_validation` | `llama_tokenize` 或 `conservative_estimate` |
| `max_tokens_field` | `max_tokens` 或 `max_completion_tokens` |
| `send_sampling_parameters` | 不接收温度等采样字段的服务可显式设为 false |
| `temperature_override`、`top_p` | 该模型配置的采样策略 |
| `chat_template_kwargs` | 本地服务支持的模板参数，例如关闭思考输出 |
| `max_schema_attempts` | schema 格式修复次数上限，最多 2 次 |
| `image_token_budget` | 非精确 tokenizer 路线的每张图像预留预算，默认 4096 |

即使服务支持结构化输出，返回 JSON 仍会在本地按 schema 检查。不完整输出、模型 ID 不匹配、超时或超出上下文会明确失败，不会悄悄切换模型或输出模式。

llama.cpp 文本上下文使用服务端模板与 tokenizer；图像及其他后端采用保守估算。估算按 UTF-8 字节和图像预留量留出空间，会比实际 token 数偏保守；不要仅提高配置数字来绕过服务实际窗口。

调用回执保留角色、配置档、请求模型、响应模型、配置版本、采样策略、耗时、使用量和结构修复过程。密钥值不写入请求文件、错误信息或回执。

## 7. 配置生效范围

模型目录保存采用完整配置版本比较和原子替换。页面 A、页面 B 同时打开时，后保存的旧版本会收到冲突，避免覆盖别人刚做的修改。

一次 worker `advance` 可以连续执行多个自动节点，直到人工决策、暂停或结束。这一批执行持有同一份配置快照；下一次队列任务才读取更新后的全局配置。调用回执中的 `config_revision` 记录本次实际使用的版本。

模型或质量策略变化可能使旧文本/评审缓存失效；仍符合生成条件的图像、音频、视频不会因为一个角色名字变化而无条件全部重做。

## 8. 图像、视频和配音

图像与视频仍通过 ComfyUI 执行具体 API 工作流。一次提交保存 `prompt_id`，随后查看该任务历史和输出。没有确认提交结果时，系统保留不确定状态，避免重复提交。

当前 ComfyUI 适配器管理本机进程与 GPU 租约；并未实现任意云视频供应商接入。配音由专用环境中的 Qwen3-TTS 受控进程执行，输入输出和回执稳定；当前不是常驻 TTS HTTP 服务。

这两种接口在工作流导出中都明确列出。要扩展新的视频或配音引擎，应实现相应的提交、状态、输出与中止语义，而不是套用聊天接口。

# 架构参考与模型服务接入依据

核查日期：**2026-10-12**。本文使用上游官方仓库和官方文档，记录设计取舍与接口依据。它不是已部署组件清单，也不表示所有供应商、模型或硬件组合都已验证。当前可用能力以本项目配置、连接检查和运行结果为准。

## 1. 设计目标

AI Studio 的核心是一个可恢复的短剧工作流：不同角色通过可配置的模型接口完成创作、评审、分镜与制作，出品人在故事、视觉和成片等关键环节作决定。模型运行服务与工作流执行器分开，Web 与 CLI 共用业务逻辑。

本项目采用的设计原则：

- **角色与模型分离**：角色描述职责，模型配置描述实际服务和能力；同一模型可承担多个角色，独立审核可绑定不同模型。
- **本地与远端通过适配器接入**：运行位置、调用协议和模型能力分别表达。
- **一个工作流状态来源**：保存阶段、决策、任务和素材版本；UI 展示这些状态，不另建一套隐含流程。
- **按镜头恢复**：成功素材可复用，失败步骤有明确原因、有限重试和预算。
- **可追溯的交付**：成片关联剧本、分镜、素材、模型身份、参数及审查记录。

## 2. 六个参考项目

| 项目 | 已核对的上游职责、框架或 UI | 本项目采用的设计取舍 |
|---|---|---|
| [Dify][dify] | API/Web monorepo；模型插件独立定义供应商凭据、模型能力与参数；提供工作流、模型管理和运行观察。 | 借鉴供应商注册、模型配置和连接验证，不增加一整套企业应用平台。 |
| [Langflow][langflow] | Python/FastAPI 后端、React/TypeScript 前端和 LFX 执行器；流程支持 API 调用与 JSON 导入导出。 | UI 与执行解耦，使用可检查的工作流配置和角色绑定；保留一个主要工作流执行器。 |
| [Toonflow][toonflow] | 当前主仓库为 Bun/TypeScript monorepo，包含 Web、Server、Desktop 和扩展工作区；支持第三方 API、本地 LLM、ComfyUI；把剧本、资产和视频关联在创作画布中。 | 借鉴人物、场景、镜头与素材的关联和预览；不把无限画布、桌面端或插件市场作为基础依赖。 |
| [MoneyPrinterTurbo][mpt] | Python 项目，使用 FastAPI、Streamlit、MoviePy；支持 WebUI、API、CLI 等入口和脚本到成片的生产链。 | 多入口共用业务层，重视配音、字幕、剪辑和成片导出；另行处理短剧连续性和独立审核。 |
| [ComfyUI][comfy] | Python/PyTorch 节点执行引擎；提供本地 API、异步队列、历史和执行消息。 | 作为图像/视频生成服务，业务层保存外部任务身份并负责恢复、质检与交付。 |
| [Drama Skills][drama] | 11 个创作 Skills，覆盖剧本、视觉、分镜、提示词、生产、剪辑和审查；创作文件与供应商适配分离。 | 借鉴产物结构、连续性约束和审查职责；由项目级工作流管理生产授权。 |

框架与接口细节见 [Dify 模型插件][dify-provider]、[Langflow 架构][langflow-architecture]、[Langflow 模型配置][langflow-models]、[Toonflow 工作区定义][toonflow-package]、[MoneyPrinterTurbo 依赖][mpt-package]、[ComfyUI API][comfy-api]。

Toonflow 的旧前端仓库已归档，当前前端在主仓库 `apps/web`，不能依据旧仓库推断当前实现。[迁移说明][toonflow-old-web]

## 3. 模型连接的边界

模型连接配置应说明服务地址、协议、模型名称、能力、上下文和超时。凭据只通过运行环境引用，不进入公开示例或工作流导出。

| 层次 | 含义 | 示例 |
|---|---|---|
| 运行位置 | 推理发生在哪里 | 本机、局域网服务、远端 API |
| 调用协议 | 请求和响应如何表达 | OpenAI Compatible、Ollama 原生 API、ComfyUI API |
| 模型身份 | 实际调用哪个模型 | 服务端模型名称、别名、版本 |
| 能力 | 该模型和服务组合能做什么 | 文本、视觉理解、结构化输出、图像、视频、语音 |
| 角色绑定 | 哪一步使用哪个模型配置 | 编剧、剧本审核、分镜导演、视觉审核 |

“使用本地模型”不意味着每次调用都重新加载权重。通常先启动独立推理服务，工作流通过本地 HTTP 请求复用它。Dify 区分供应商和模型配置；Langflow 的全局模型配置支持兼容端点、本地 Ollama 和自定义组件。[Dify 接入规范][dify-provider]、[Langflow 模型配置][langflow-models]

文本/视觉理解适配器负责消息、结构化输出、参数转换和结果校验。视频生成适配器负责提交、查询、获取素材和服务实际支持的取消能力；它不需要伪装成聊天接口。

ComfyUI 的 `POST /prompt` 会验证工作流并排队，返回 `prompt_id`；`/history/{prompt_id}`、`/queue` 和 `/ws` 用于检查任务。恢复时应查询已保存的任务，而不是重新提交。其 `/interrupt` 停止当前执行，不能当作任意共享任务的通用取消 API。[官方路由][comfy-api]

## 4. 三种本地文本模型服务

以下是上游运行方式的简例，不是自动安装脚本。先准备与所选运行时兼容的模型、驱动及依赖，并按显存调整上下文和并发。示例模型路径需要替换；端口显式指定，避免依赖上游默认值。多媒体能力还取决于模型与运行时支持。

### llama.cpp / llama-server

适用于通过 GGUF 权重启动本地服务。已有编译好的二进制和模型时：

```bash
./build/bin/llama-server \
  --model ./models/chat.gguf \
  --alias studio-chat \
  --host 127.0.0.1 --port 8090 \
  --ctx-size 8192 --parallel 1 \
  --n-gpu-layers 99 --jinja
```

客户端基址为 `http://127.0.0.1:8090/v1`，模型名称为 `studio-chat`。`--n-gpu-layers` 表示请求卸载到 GPU 的层数，实际可用程度取决于编译后端和显存。[server README][llama-server]

`/v1/chat/completions` 支持 `response_format`。llama.cpp 原生 schema 写法之一是：

```json
{
  "type": "json_object",
  "schema": {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": false
  }
}
```

应由适配器按服务版本选择正确的 `response_format` 形状。官方 grammar 文档说明 JSON Schema 仅支持一个子集，且 schema 不自动成为提示词的一部分；应用仍需验证结果，并在提示词中说明字段含义。视觉输入还需要受支持模型和相应多模态配置。[JSON Schema 与 grammar][llama-grammar]

### Ollama

未运行服务时，在一个终端启动：

```bash
ollama serve
```

在另一个终端下载示例模型：

```bash
ollama pull qwen3:8b
```

兼容接口基址为 `http://127.0.0.1:11434/v1`，模型名使用实际已安装的 tag。官方兼容示例中的 SDK 占位 API Key 不构成本地鉴权；部署时按访问环境管理服务边界。[CLI][ollama-cli]、[兼容接口][ollama-openai]

原生 `POST /api/chat` 使用 `format: <JSON Schema>`；兼容接口使用 `response_format`。上下文大小不能靠 OpenAI 请求中的输出长度参数设置，可通过 Modelfile 的 `PARAMETER num_ctx` 创建相应模型配置。官方当前文档说明 Ollama Cloud 尚不支持 structured outputs，不应把本地能力直接推广到其云模型。[结构化输出][ollama-structured]、[上下文设置][ollama-openai]

### vLLM

准备好受支持的 Hugging Face 格式模型目录后：

```bash
vllm serve ./models/hf-chat-model \
  --served-model-name studio-chat \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 8192
```

客户端基址为 `http://127.0.0.1:8000/v1`，使用 `studio-chat`。模型需要合适的 chat template；模型未提供时可显式配置 `--chat-template`。GPU 数量、精度及量化应按硬件和模型选择，不由“支持兼容 API”推导可运行性。[在线服务][vllm-serving]、[serve 参数][vllm-cli]

结构化输出可使用：

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "check",
    "schema": {
      "type": "object",
      "properties": {"ok": {"type": "boolean"}},
      "required": ["ok"],
      "additionalProperties": false
    }
  }
}
```

上例放在 `response_format`。另可通过额外请求体 `structured_outputs.json` 传 schema。官方文档说明结构化输出默认可用；旧 `guided_json` 等字段在 v0.12.0 已移除，不能沿用旧教程。[结构化输出][vllm-structured]

vLLM 旧 `serving/openai_compatible_server/` 文档地址当前重定向至下方列出的 `serving/online_serving/`。实际部署应固定运行时版本，并对使用的 schema 做调用验证。

## 5. UI 与运行逻辑

出品人界面优先回答四个问题：哪个项目、到哪一步、生成了什么、现在需要做什么。用故事/人物场景/分镜/成片组织内容；模型地址和原始回执放在设置或技术详情中。Drama Skills 的官方设计强调常驻内容阅读区、明确保存语义和清楚的下一步。[创作 UI 设计][drama-design]

建议将以下状态明确显示：

- 模型连接：未配置、可连接、校验失败及具体原因。
- 工作流：待开始、执行中、等待选择、等待资源、失败、完成。
- 镜头：素材与版本、审查问题、有限修复记录、当前采用版本。
- 交付：初版生成、待出品人确认、已确认终版。

“模型列表请求成功”只说明该接口可用；结构化输出和视觉能力应分别验证。保存配置或草稿不应隐式启动生成。流程导出使用凭据引用；Langflow 官方文档明确区分导出字面量 API Key 和导出变量名，可作为设计依据。[流程导出][langflow-export]

## 6. 许可与来源管理

| 上游 | 核查时的许可边界 |
|---|---|
| Dify | Dify Open Source License，基于 Apache 2.0 并含附加条件；多租户服务及前端标识有特别要求。见 [LICENSE][dify-license]。 |
| Langflow | MIT；具体复用遵循其 [LICENSE][langflow-license]。 |
| Toonflow | 当前主仓库 MIT；README 明确旧版本继续适用当时的 AGPL 或 Apache 与附加协议。见 [仓库许可说明][toonflow]。 |
| MoneyPrinterTurbo | MIT。见 [LICENSE][mpt-license]。 |
| ComfyUI | GPL-3.0。见 [LICENSE][comfy-license]。 |
| Drama Skills | MIT。见 [LICENSE][drama-license]。 |

架构参考不等于复制上游代码。若引入实现、模板或其他资产，应记录准确来源、版本和许可证，保留要求的署名。模型权重、训练数据和生成素材另有许可或使用条件；不能从框架许可证推断其全部可用范围。本仓库发布源代码与示例配置，不发布真实凭据、私人素材、运行数据库或模型权重。

## 官方来源

所有链接核查日期均为 **2026-10-12**；上游 main/master 和 latest 文档会继续变化。

[dify]: https://github.com/langgenius/dify
[dify-provider]: https://github.com/langgenius/dify-docs/blob/main/en/develop-plugin/dev-guides-and-walkthroughs/creating-new-model-provider.mdx
[dify-license]: https://github.com/langgenius/dify/blob/main/LICENSE
[langflow]: https://github.com/langflow-ai/langflow
[langflow-architecture]: https://github.com/langflow-ai/langflow/blob/main/AGENTS.md
[langflow-models]: https://docs.langflow.org/components-models
[langflow-export]: https://docs.langflow.org/concepts-flows-import
[langflow-license]: https://github.com/langflow-ai/langflow/blob/main/LICENSE
[toonflow]: https://github.com/HBAI-Ltd/Toonflow-app
[toonflow-package]: https://github.com/HBAI-Ltd/Toonflow-app/blob/master/package.json
[toonflow-old-web]: https://github.com/HBAI-Ltd/Toonflow-web
[mpt]: https://github.com/harry0703/MoneyPrinterTurbo
[mpt-package]: https://github.com/harry0703/MoneyPrinterTurbo/blob/main/pyproject.toml
[mpt-license]: https://github.com/harry0703/MoneyPrinterTurbo/blob/main/LICENSE
[comfy]: https://github.com/Comfy-Org/ComfyUI
[comfy-api]: https://docs.comfy.org/development/comfyui-server/comms_routes
[comfy-license]: https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE
[drama]: https://github.com/zenstory-ai/drama-skills
[drama-design]: https://github.com/zenstory-ai/drama-skills/blob/main/DESIGN.md
[drama-license]: https://github.com/zenstory-ai/drama-skills/blob/main/LICENSE
[llama-server]: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
[llama-grammar]: https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md
[ollama-cli]: https://docs.ollama.com/cli
[ollama-openai]: https://docs.ollama.com/api/openai-compatibility
[ollama-structured]: https://docs.ollama.com/capabilities/structured-outputs
[vllm-serving]: https://docs.vllm.ai/en/latest/serving/online_serving/
[vllm-cli]: https://docs.vllm.ai/en/latest/cli/serve/
[vllm-structured]: https://docs.vllm.ai/en/latest/features/structured_outputs/

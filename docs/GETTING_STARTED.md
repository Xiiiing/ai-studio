# 首次使用与创作操作

[项目首页](../README.md) · [文档中心](README.md) · [English](GETTING_STARTED.en.md)

本指南说明从空配置开始，接入自己的模型、提交项目、查看产物和处理审核的完整路径。控制层不包含模型权重，也不依赖开发者的私有服务器。

## 安装与启动

准备 Linux、Python 3.12 和 Node.js 24（构建 UI 使用，与 CI 一致）。控制层无需 GPU；媒体编解码、中文字体以及本地推理依赖见[部署说明](DEPLOYMENT.md)。

```bash
git clone https://github.com/Xiiiing/ai-studio.git
cd ai-studio
python3.12 scripts/bootstrap.py
./drama models
./drama workflow --json
```

初始化只在配置缺失时从 `config/factory.empty.example.json` 创建 `config/factory.json`，保留已有配置；构建产物写入 `ui/factory/dist`。空配置没有已启用的模型，需要先在工作台完成接入。`config/factory.example.json` 提供更多配置格式示例，`config/factory.api.example.json` 展示全 API 路线；示例模型别名必须替换成服务实际提供的 ID。

若运行服务器已安装与代码匹配的预构建 UI，可以使用 `python3.12 scripts/bootstrap.py --skip-ui-build`。该选项不会提供缺失的 UI 资源。`--skip-install` 仅初始化配置。

分别在两个终端运行：

```bash
# 工作台
envs/factory/bin/python -m factory.web
```

```bash
# 后台执行器
envs/factory/bin/python -m factory.worker
```

打开 [http://127.0.0.1:8787/](http://127.0.0.1:8787/)。长期运行、SSH 转发和升级步骤见[部署说明](DEPLOYMENT.md)。Web 和 worker 共用状态库，两者都需要正常运行才能完成制作。

## 选择模型路线与保管密钥

- **全部使用 API**：使用已适配的文本/视觉服务、百炼图像与配音、MiniMax 视频；需要自己的账号、开通的模型和网络，无需本机 GPU。
- **自建模型**：文本连接 llama.cpp、Ollama、vLLM、LM Studio 或兼容接口；图像/视频使用兼容的 ComfyUI 工作流；本地配音使用独立 Qwen3-TTS 环境。
- **混合路线**：每个环节独立选择。`localhost` 指后端所在机器；远端模型不能误填为开发机本地服务。

私有环境文件不存在时，按需创建：

```bash
cp config/runtime.env.example config/runtime.env
chmod 600 config/runtime.env
```

只填写自己使用的密钥；已有文件应直接编辑，不要覆盖。配置仅保存 `api_key_env` 变量名。Web、worker 和 CLI 读取这个文件，进程环境变量优先；修改后重启需要这些变量的进程。更多账号地域、工作空间、授权和费用恢复说明见[用户模型指南](USER_MODELS.md)。

## 配置模型

进入工作台的“模型与连接”：

1. 添加或编辑连接，填写服务类型、地址、服务端模型 ID、上下文和能力。
2. 为各个角色选择模型，视觉质检必须绑定支持视觉输入的配置。
3. 点击“保存配置”。保存不开始生产，也不会自动下载或启动模型。
4. 对已保存的连接点击“测试连接”，核对模型列表和 ID；测试失败时修改配置、重新保存后再测试。草稿不能直接进行连接测试。
5. 在“视频模型”中添加 MiniMax 连接或选择本地 Wan，为“视频生成”指定连接；点击“预检配置”检查保存的配置和密钥变量。连接 ID 以实际配置为准，空配置没有示例连接。
6. 进入项目的“制作工作流”或“团队与回执”，查看角色的输入、产物和执行记录。成员卡显示当前分配；历史回执保留当次实际模型，不随默认配置变化。

Base URL 是完整 API 前缀，不统一追加 `/v1`。例如 DeepSeek 可使用服务根，百炼保留 `/compatible-mode/v1`，Ollama 原生使用服务根或 `/api`。百炼等目录协议不同的服务可设 `model_discovery: "skip"`，预检会明确显示“未验证”，实际调用仍校验响应模型与 JSON 结构。

MiniMax 的当前适配器使用 v1 图生视频协议，支持任务、文件 ID 与可恢复下载；这不表示自动支持所有其他视频供应商或 H3 的不同协议。`motion_comic` 仍使用关键帧与程序运镜，云视频选择用于真人和二维动画路线。详见模型文档中的支持组合与恢复说明。

也可使用 CLI：

```bash
./drama roles
./drama models probe YOUR_TEXT_PROFILE
./drama models probe YOUR_VIDEO_PROFILE --kind video
./drama models export --file runtime/models-edit.json
# 编辑导出文件中的 llm_profiles 和 llm_roles
./drama models apply --file runtime/models-edit.json
```

导出文件保留配置版本；其他页面已修改配置时，旧文件会被拒绝，先重新导出再修改。真实密钥只放在服务环境变量中，配置仅保存 `api_key_env` 名称。详见[模型接口说明](MODEL_PROVIDERS.md)。

## 阅读工作流与 Agent 输入输出

项目首页使用固定布局流程图展示故事策划、剧本与分镜、视觉确认、镜头与后期、终审交付五个阶段。节点端口和方向箭头表达真实拓扑中的数据流向，执行中的阶段显示流动线与流动点，返修使用金色连线；无需缩放或平移。点击阶段在右侧查看真实子步骤、输入材料、交付内容、参与角色与执行记录；窄屏点击后直接定位下方详情，并可返回流程图。暂停和等待人工处理时动效停止，减少动态效果设置会关闭动画。自动刷新保留手动选择和正在阅读的文件。

输入与输出文件均可点击“阅读文件”。Markdown 支持标题、表格、列表和代码块，JSON 可切换正文与原文；支持放大阅读、媒体预览和下载。文本预览上限为 2 MiB，超限时下载阅读；文件缺失或读取失败可重试。

“执行记录”按调用及重试次数展示已保存的请求、响应、解析结果和回执，包括提示词与消息原文。当前业务材料与当次模型输入分别展示；旧调用缺少步骤归属时标为角色历史，不推测所属步骤。团队页中的执行记录可定位回对应步骤。查看内容不会启动生成或改变终审决定。

## 提交创作任务

```bash
./drama run \
  --brief "原创邻里悬疑，两个成年角色、少量场景，开场有钩子，结尾完成反转，适合动态漫和画外音。" \
  --title "邻里悬疑" \
  --format motion_comic \
  --seconds 60 \
  --gpu-minutes 120 \
  --wall-minutes 180 \
  --max-attempts 3 \
  --request-id first-episode
```

工作流需要人工处理时，工作台展示候选、审核依据和有效操作。新项目默认 AI 代管故事与视觉；总制片未配置时明确回到人工处理。创建时使用 `--team-mode guided` 可全程人工选择故事与画风。

审核待办可以直接选择“按全部审核建议修改”、具体问题或当前阶段的常用修改方向，再确认提交，无需填写文字；也可保留选项并补充自定义要求。视觉重新生成和成片返修、重剪同样支持选项。选择表达修改重点，作品仍需通过独立审核；页面更新时会保留已选要求，并在版本冲突后要求核对再提交。

创作审核默认每批最多 4 轮（3 次自动返修）：审稿人指出问题，创作角色按原稿和意见重做，再送独立审核，通过后继续。超过上限时保留历史，由已配置的总制片模型分析返修方案、理由、风险和需要你决定的取舍；也可点击“请 AI 分析并推荐”。你可以采纳 AI 方案继续一批返修，或使用自己的审核选项。终片确认、预算增加和新增费用授权由你决定。`creative.max_review_rounds` 支持 1–6，旧配置的显式上限继续有效。

CLI 可用：

```bash
./drama status PROJECT_ID
./drama choose PROJECT_ID --type story --choice PROPOSAL_ID --decision-id STORY_GATE_ID
./drama choose PROJECT_ID --type style --choice STYLE_ID --decision-id STYLE_GATE_ID
./drama outputs PROJECT_ID
```

实际编号来自 `status`，不要使用上述占位符。制作中可请求暂停并在安全边界恢复；详情页提供预算、失败原因、镜头状态和局部返修。终审也可以选择修改，并给出反馈。

## 暂停、恢复与交付

制作中在工作台请求暂停，后台任务到达安全边界后停止；从项目详情恢复。暂停不是立即杀死模型进程。失败时先查看具体步骤、模型回执和镜头状态，再选择对应的恢复或返修操作，避免重复提交未知的付费任务。

初版成片与正式交付是两个状态。出品人检查故事完整性、人物场景连续性、逐镜画面、配音、字幕、剪辑和费用记录后再确认；有问题可定向返修或重剪。媒体文件、制作清单与历史回执随项目保存在 `production/`，不进入 Git。

## 常见问题

| 现象 | 先检查什么 |
| --- | --- |
| 首页返回 503 | `ui/factory/dist` 是否已经构建，构建资源是否与源码匹配。 |
| 项目不推进 | worker 是否运行、角色绑定是否完整，当前是否等待人工或费用授权。 |
| “检查通过”但生成失败 | 预检不是推理验收；核对模型能力、实际 ID、schema、地域、权限与回执。 |
| 外部模型无法连接 | 后端能否访问地址，网络范围与允许主机是否匹配，带鉴权的远端接口是否使用 HTTPS。 |
| 字幕或封面出现方块 | 制作机是否有可用中文字体，见[字体说明](DEPLOYMENT.md#中文字幕与封面字体)。 |
| 保存配置出现版本冲突 | 重新读取或导出最新配置，核对变更后再保存，不覆盖其他窗口的新版本。 |

需要定位代码和状态流时，继续阅读[架构导览](ARCHITECTURE.md)；完整接口见 [API](API.md)。

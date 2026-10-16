# 第三方组件与来源

本仓库发布 AI Studio 的工作流、接入适配器、界面、测试和示例配置。模型权重、生成媒体、用户数据库、凭据及第三方完整运行环境不包含在源码发布中。

项目整体尚未另行指定统一开源许可证；公开托管不改变第三方既有条款。以下文件与上游来源独立管理。

## 运行组件

| 组件 | 本项目使用方式 | 官方来源与许可入口 |
| --- | --- | --- |
| LangGraph | Python 执行图、决策中断、SQLite 检查点 | [仓库](https://github.com/langchain-ai/langgraph)、[LICENSE](https://github.com/langchain-ai/langgraph/blob/main/LICENSE) |
| React / React DOM | 制作工作台组件与渲染 | [仓库与 MIT LICENSE](https://github.com/facebook/react) |
| TanStack Query | 工作台服务端状态、刷新与失效 | [仓库与 MIT LICENSE](https://github.com/TanStack/query) |
| React Flow / xyflow | 历史工作台采用的图交互参考与依赖；当前主分支已使用自有固定阶段图，不在当前 UI 依赖清单中 | [仓库与 MIT LICENSE](https://github.com/xyflow/xyflow) |
| react-markdown / remark-gfm | Markdown 文件阅读与 GFM 表格、列表等解析 | [react-markdown](https://github.com/remarkjs/react-markdown)、[remark-gfm](https://github.com/remarkjs/remark-gfm)，MIT |
| Lucide | 工作台矢量图标 | [仓库与 ISC LICENSE](https://github.com/lucide-icons/lucide) |
| Vite / Vitest | UI 构建与开发测试 | [Vite](https://github.com/vitejs/vite)、[Vitest](https://github.com/vitest-dev/vitest)，MIT |
| TypeScript | UI 类型检查，仅构建依赖 | [Apache-2.0 LICENSE](https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt) |
| llama.cpp | 独立本地模型服务，HTTP 调用 | [仓库](https://github.com/ggml-org/llama.cpp)、[LICENSE](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) |
| ComfyUI | 独立图像/视频服务；本地节点使用其 Python API | [仓库](https://github.com/Comfy-Org/ComfyUI)、[GPL-3.0 LICENSE](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE) |
| FFmpeg / PyAV | 受控本地媒体制作与检查 | [FFmpeg legal](https://ffmpeg.org/legal.html)、[PyAV](https://github.com/PyAV-Org/PyAV) |
| Qwen3-TTS | 独立 Python 环境的本地配音 | [仓库](https://github.com/QwenLM/Qwen3-TTS) |
| LatentSync | 可选旧口型适配器；不属于默认动态漫流程 | [仓库](https://github.com/bytedance/LatentSync) |

`nodes/wan_tiled_vae` 在 ComfyUI 原生 tiled decoder 上增加受限的内存估算；`nodes/pilot_device_loaders` 调用 ComfyUI 模型加载 API。ComfyUI 完整源代码由独立部署提供，未复制进本仓库。不要从控制层的公开托管方式推断这些上游组件或插件的许可已经被重新授权。

## 模型与工作流参数

下载脚本和配置记录了模型仓库、revision、文件名和校验值。对应模型包括 FLUX.2 klein、Wan2.2、FastWan、Qwen3-TTS 和本地 LLM。具体权重的许可证以对应官方模型页和下载版本为准。

- [FLUX.2 klein](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)
- [Wan2.2](https://github.com/Wan-Video/Wan2.2)
- [FastWan](https://huggingface.co/FastVideo/FastWan2.2-TI2V-5B-FullAttn-Diffusers)
- [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)

`workflows/fastwan_profile.json` 保存公开上游的来源和原生 ComfyUI 参数映射。对应权重与上游实现单独下载；本仓库不包括完整 FastVideo 上游源码。

可选口型流程所需的人脸检测、关键点及其他预训练权重可能有独立使用限制。源码许可不能替代对这些模型版本的核对。

## 架构与设计参考

Dify、Langflow、Toonflow、MoneyPrinterTurbo、ComfyUI、Drama Skills 的职责分层和创作界面是本次研究依据；没有把这些平台的整套前后端复制到本项目。具体核查日期、框架、采用的原则和许可证出处见 [参考架构研究](docs/REFERENCE_ARCHITECTURES.md)。

如果未来直接引入第三方代码、模板、图片、字体或图标，应在引入时保存对应文件的来源和许可证，而不是只引用这个总表。

2026-10-15 重构研究固定到 Dify `1f3fdbc0d32ff7a52f94a2a34e00183e698f1c2f`、Toonflow `d2e94aca7b59659ff6b4ca15f03cf134ec7f0e1d` 和 xyflow `3d35b57317576b0916c0bfeaaedd573aaacc2839`。借鉴动作表单、委派事件、节点交互，业务实现自行编写；未复制 Dify 前端或其品牌资源。当时 React Flow 通过依赖引入；当前工作台已改为自有固定阶段图，实际依赖以 `package.json` 与 `package-lock.json` 为准。具体源码入口见参考架构研究的重构补充。

2026-10-16 的项目成果与导出入口修正参考 OpenCut、InvokeAI 和 shadcn/ui 的官方固定版本源码，仅借鉴操作位置、作品查看与导航分区，未复制组件源码或增加依赖。固定版本、具体文件及适用边界见 [0.0.6 界面记录](docs/UI_RESULTS_WORKSPACE_0_0_6.md)。

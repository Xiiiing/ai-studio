# Qwen3.8 视觉与 H3 本地质量配置

本阶段只修改代码、公开示例和隔离验证环境。**未部署，未修改 cami/Wkp 的活动配置，未重启共享模型服务。** 之前 H3 turbo4 接入和部署属于[历史验收](WKP_H3_MODEL_AUDIT.md)，不能据此认为本阶段已经上线。

## 匹配的视觉组件

Wkp 已有 `Qwen3.8-27B-Q8_0.gguf` 的 GGUF 架构为 `qwen35`，模型名为 `Qwen3.8-27B`。原来的通用 `mmproj-BF16.gguf` 元数据属于 Qwen3.6-35B-A3B，不能与 Qwen3.8 配对。

新增组件来自 [ggml-org 的固定版本](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF/blob/71bc7b627595dc8a91039addd9c791ae548d6747/mmproj-Qwen3.8-27B-BF16.gguf)：

- revision：`71bc7b627595dc8a91039addd9c791ae548d6747`
- 文件：`mmproj-Qwen3.8-27B-BF16.gguf`，931145888 字节
- SHA-256：`de2a49866988ea272c43dc2a43ee2662c25725a717ca5b9059ea420a97f53fe8`
- 元数据：`general.name=Qwen3.8-27B`、`general.type=mmproj`、`clip.has_vision_encoder=true`、`clip.projector_type=qwen3vl_merger`

组件保存在 Wkp 的独立 `runtime/local-quality-20261016/models` 目录，没有替换通用文件或搬运原模型。下载器固定版本、大小和校验和，使用独占临时文件，校验后发布；已有不匹配文件会保留并报错。GGUF 检查只读取有界头部，不加载权重。

```bash
# 在拥有这些模型的 Wkp 执行；不会启动或修改服务。
python scripts/verify_local_vision.py \
  --model /root/jialin/Rwork/llama.cpp/build/models/Qwen3.8-27B-Q8_0.gguf \
  --download-directory /root/jialin/drama_studio/runtime/local-quality-20261016/models
```

Qwen3.8 已在隔离双 GPU 进程中加载匹配组件。实际模型别名 `ai-studio-qwen38-vision`，`/props` 返回 vision/video 为 true、audio 为 false。单张写实人物参考审核用时 28.054 秒，输入 1109、输出 127 tokens；识别出蓝衬衫、咖啡桌、白杯，并指出手指可能融合。该证据证明图像推理可用，不证明所有视觉判断正确。

## 分工和配置

[配置片段](../config/wkp-local-quality.example.json) 分别列出临时视觉测试、推理路由、服务器视觉 profile、H3 参数和画布尺寸，不能把整个片段当作完整应用配置直接覆盖生产文件。

- 新安装配置建议 `visual_critic → vision → ai-studio-qwen38-vision`；编剧和文字审稿继续分别使用现有两条 Qwen3.8 服务。
- 推理服务可用顶层 `model_routes.vision` 指向已配置的 `llm_profiles` 键，例如 `qwen38_vision`。模型名称和端点来自用户配置，客户端仍核对实际目录、模态和响应模型。
- 16K 视觉窗口配置 `image_token_budget=1536`。应用将审核图缩到 512×768 以内，按窗口选择至少四个视频时间点；不能容纳参考图和最低抽样数量时明确返回不可审核。
- `scripts/local_vision_smoke.py --config <vision_test JSON> --image <reference> --media <video> --output <isolated-directory>` 使用共享 GPU 租约，启动一个随机 loopback 端口的自有进程，校验模型和视觉能力，执行图片检查和应用的 `visual_critic`，写回执后退出。不安装常驻服务。
- `configure_wkp_h3.py` 默认要求正式档和精确 Qwen3.8 视觉模型 ID，当前生产路由尚为 Qwen3.6 时会拒绝切换。质量档和所需视觉模型可通过 CLI 显式指定。`--apply` 仍需独立部署授权、无活动任务且 Web/worker 已停止，本阶段未执行。

临时测试使用 GPU 5、6，不代表未来应常驻占用生产 GPU 池。后续部署需决定给既有 Qwen3.8 审稿实例启用匹配 projector、共享文字与视觉队列，或配置专用视觉实例，并重新验收真实网关。此阶段不调整现有服务的 GPU 归属。

## H3 正式档

[Comfy 官方指南](https://github.com/Comfy-Org/docs/blob/main/tutorials/video/minimax/minimax-h3.mdx) 建议基础权重关闭加速 LoRA、使用 20 步；参考一致性仍有漂移时可尝试 25 步。

| 参数 | 预览档 | 正式档 |
| --- | --- | --- |
| revision | `wkp-dualgpu-turbo4-v1` | `wkp-dualgpu-base-v1` |
| quality | `preview` | `production` |
| LoRA | 现有 turbo4 | 不加载 turbo LoRA |
| steps | 4 | 默认 20，可选 20–50 |
| sampler | Euler | Euler 或 Heun |
| shift_video / shift_audio | 12 / 3 | 默认 12 / 3，可选 1–20 / 1–10 |
| 画布 | 用户配置 | 建议 768×1344，32 对齐且不超过该像素面积 |
| 帧率与长度 | 24 fps，`17k+5` 帧 | 同左 |

新建 H3 连接的预设为正式档。已有无 `quality` 的连接维持原 turbo4 图和缓存身份；编辑后不切档不会无意变更缓存。参数真正进入图、生成记录、请求哈希和输出回执，修改参数不能复用旧结果。服务端只接收可重建的规范图；旧推理服务不能假装支持新正式档。

模型、分工、尺寸和生成参数均保留配置入口。管理式 H3 适配器限定已验证权重；其他本地模型仍可通过既有外部 ComfyUI 工作流连接配置，不通过任意权重路径绕过服务器校验。H3 自身的 Qwen3-VL 32B 编码器是生成模型依赖，与 Qwen3.8 审核器分工不同，不能直接替换。配音继续由 Qwen3-TTS 执行。

本地运行的是 H3-Base-FL2VA。官方托管 Context-IR 和 Regenerate-2K 不包含在此链路中；不将本地结果宣称为完整托管 2K 系统。[MiniMax 原始说明](https://github.com/MiniMax-AI/MiniMax-H3)

## 人物镜头验证

验证使用 Wkp FLUX 生成的原创写实成年人物首帧，人物在咖啡馆坐着、穿深蓝衬衫、手边有白杯；不是生产素材，也不是声称真实拍摄的演员。提示词要求固定机位、自然眨眼、从窗外缓慢转向镜头并微笑，手与杯保持在桌上。

768×1344、124 帧、20 步正式档首次测试在首步的 INT8 eager 计算中显存不足；增加预留显存到 12 GiB 后仍失败。失败任务回执保留在独立目录，临时进程已退出；没有降低分辨率替代验收。

项目内 `nodes/pilot_device_loaders/int8_memory.py` 仅由 H3 自有子进程启用，通过 Comfy Kitchen 的公开后端注册 API 替换该进程的 eager INT8 调用。保留完整形状的激活函数和 ConvRot 旋转，再分块调用原生量化、整数累加、缩放和 bias，预分配输出，避免完整 INT32 中间结果与多个整幅输出同时驻留。共享安装和其他任务不变。最初将 ConvRot 一并分块时，GPU 数值对比发现不同舍入，已修复；修复后原生 CPU/GPU 的 12 组 FP16/BF16/FP32、ConvRot 开关、标量/逐通道缩放组合均逐位相等，最大差值为 0。该样本数值验证不等于所有可能张量的数学证明。

可配置服务器端 `production.h3_reserve_vram_gib`，范围 8–20 GiB、兼容默认 8；新正式配置建议 12。客户端不覆盖服务器的显存设置。预留显存本身未解决上述 OOM，正式档还需要配套的进程内 INT8 适配。

真实测试入口：

```bash
python scripts/h3_quality_smoke.py \
  --config config/wkp-local-quality.example.json \
  --image /path/to/independent-human-reference.png \
  --output /path/to/isolated-checkout/runtime/human-production \
  --gpu-cards 5 6 7
```

只选择空闲且可取得共享租约的两张 GPU；默认墙钟 4200 秒、累计 GPU 8400 秒，输出必须在当前隔离 checkout 的 `runtime` 下。保留图、提交 ID、产物哈希和完整技术检查，未知提交不自动重发。实际双卡和退出后的用量记录在 `validation.json`。

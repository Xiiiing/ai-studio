# Wkp 模型核实与 H3 接入

核实日期：2026-10-16，中国标准时间。先通过已有 mTLS 模型入口及获授权的 Wkp Runner 进行只读检查；用户随后明确授权部署并做有界 H3 验收。没有读取或复制模型 Bearer 凭据、模型权重、生产数据库或生产素材。部署及真实测试结果见下文。

## 在线模型

| 路由 | 真实模型 ID | 服务声明输入 | 单槽上下文 |
| --- | --- | --- | --- |
| primary | `qwen3.8-27b-q8-opt-2dflash` | text | 131072 |
| critic | `ai-studio-qwen38-23` | text | 16384 |
| vision | `Qwen3.6-35B-A3B-UD-Q4_K_S` | text、image、video | 32768 |

证据来自每个路由的 `/v1/models` 和 `/props`，不是配置文件里的能力勾选。Qwen3.8 当前两个实例没有图像输入能力，所以保留 Qwen3.6 作为 `visual_critic`；不能仅将名称改成 Qwen3.8。检查模型目录时，若服务明确声明不支持图片而配置要求视觉，会返回 `vision_unsupported`。目录未提供能力时保持未知，不将其视为已验证。

## 原有 ComfyUI / MiniMax H3

检查到下列真实文件：

| 依赖 | 文件 | 字节 |
| --- | --- | ---: |
| 主模型 | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 20970379616 |
| 文本/视觉编码器 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15687142551 |
| 视频 VAE | `minimax_h3_video_vae_fp16.safetensors` | 5207808496 |
| turbo4 LoRA | `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` | 1956192992 |

主模型、编码器和 VAE 在 Wkp 的 ComfyUI 模型目录；LoRA 在原短剧工程目录。沿用文件位置，不搬运权重。可用 `DRAMA_H3_LORA_DIR` 明确指定服务器现有 LoRA 目录；否则先查当前工程模型目录，再查原短剧工程目录。设备放置节点要求恰好两张显式租用的可见 GPU。

原执行器读取 `h3_i2v_titan_dualgpu.json`，使用 `MiniMaxH3ImageToVideo.first_frame`、turbo4 LoRA、4 步 Euler、视频/audio shift 12/3、双卡编码器与 VAE、分离 AV latent、tiled 视频解码及 MP4 输出。新适配器保留这条视频链路，去掉与业务交付无关的 latent 文件保存；不把画像试验函数当作视频生成器。配音继续使用 Qwen3-TTS，不启用 H3 音频解码。

核实的 ComfyUI revision：`9a9fdb10ed144ce760d9682cb247526ea23cc525`。旧模板 SHA-256：`d9f93dce6511024414a7acc70a829ff02eb5ee4e1ff4956d5f225ef7802cb9e7`。旧 H3 执行器 SHA-256：`5a91cf6259883db2b723317132ed9ad33f606cd6be5ff1fcc4abdb57df57debe`。

## 新入口与运行边界

核实当时 Wkp `drama_studio` 为 `339f6c7fa8b0ffed279d597066c3b3e3181f9e70`，正在运行 `ai-studio-inference.service`。该版本 GPU API 只接受 FLUX/Wan 图，没有开放 H3。旧 ComfyUI 监听端口需要其自身鉴权，不能假设是无需认证的共享 H3 API。

本次代码为 GPU API 增加固定 H3 工作流校验及双卡调度，并在健康信息中分别报告 H3/Wan。客户端必须取得匹配的 H3 模型、revision、依赖和双卡声明才能创建新任务；缺失时返回 `remote_h3_unavailable`。任务仍使用原持久 ID、源图哈希、GPU 用量和文件校验，不隐式改用 Wan 或云 API。已有任务的续查不依赖重新提交。

H3 生成长度上取整到 `17k+5` 帧，技术检查按生成长度验收；剪辑仍按批准的镜头时长。生成画布需对齐 32 像素，像素量不超过原节点的 768×1344 上限。默认 352×640 是对原低分辨率模板的竖屏配置；下方独立短片已验证此方向的真实生成与技术检查，不能据此保证完整短剧的艺术质量。

## 发布和验收

本地 Python、UI 和浏览器 fixture 检查只能证明代码契约及合成媒体流程。模型目录检查、文件存在和旧模板也不能证明本次图生视频已成功运行。

要使已有 cami 安装真正生效，需发布受审查的 H3 推理服务版本，再通过 `scripts/configure_wkp_h3.py` 核对在线能力。无未完成任务且 cami Web/worker 已停止时，`--apply` 才允许写入新配置；配置并发变化或用户自定义连接会被保留并报错。该工具不部署代码、不停止或重启服务、不生成内容。最后用独立测试目录执行一次有界 H3 样片，验收首帧一致性、运动、实际帧数、双卡用量、模型回执以及恢复行为，再开展正式制作。

## 授权部署与真实验收结果

用户明确授权后，在确认 cami 没有活动任务且 Wkp 推理记录均已终结的条件下，备份 cami 配置、UI 构建和 Wkp 待改源码。Wkp 只发布 `ebe5d01bb26c4283140a431b92fad9f121f6997c` 的 `factory/gpu_service.py`、`factory/scheduler.py`、`studio/graphs.py` 和新增 `studio/h3.py`；逐文件校验原版本哈希后重启 `ai-studio-inference.service`。Wkp Git HEAD 仍为上述原版本，实际发布文件和备份由私有部署 manifest 记录，不将局部代码部署误写为整仓库升级。Qwen 共享服务未重启。

cami 应用同一已通过 CI 的代码，迁移检查读取在线模型与 H3 能力后切换 `video_generator → wkp_h3`，将 `local_wan.enabled` 设为 false，并恢复 Web/worker。真实浏览器只读检查确认页面显示 H3 分工、双卡工作流提示及 Qwen3.6 视觉分工。

独立测试使用新建的合成红球首帧，不创建生产项目、不读取生产素材；限制为 900 秒墙钟和 1200 秒累计 GPU 时间。Wkp 实际使用 GPU 4、5，完成原 H3 turbo4 推理：

| 项目 | 实测 |
| --- | --- |
| 模型 / 工作流回执 | `comfyui_h3` / `MiniMax-H3` / `wkp-dualgpu-turbo4-v1` |
| 原始视频 | 352×640、24 fps、73 帧、3.041667 秒 |
| 本地完整解码与技术检查 | 通过，无黑帧或时长、尺寸错误 |
| 累计 GPU 时间 | 452.661 秒，约 7.54 GPU 分钟 |
| 客户端墙钟时间 | 约 228.4 秒 |
| 缓存复用 | 相同产物哈希，未增加 GPU 用量 |
| 持久任务恢复 | 移走测试缓存后，仅 GET 原任务 ID 取回相同产物，无 POST 新任务，GPU 用量不增加 |

人工查看首、中、尾帧，首帧中的红球与背景得到保留，红球向右移动。这验证了简单动作和首帧接入，不代表复杂人物动作或全剧一致性已验收。测试结束后，任务为 completed，拥有的 ComfyUI 子进程已退出，GPU 4、5 的租约已释放；cami Web/worker 和 Wkp 推理服务均正常运行。

# Wkp 服务接入与 0.1.0 开发基线

2026-10-17 使用现有授权 mTLS 连接只读核对 `/health` 和模型目录：primary 的模型 ID 是 `qwen3.8-27b-q8-opt-2dflash`，服务声明上下文 131072；vision 是 `qwen3.8-27b-q8-vision`，上下文 32768，声明图片和视频输入。长集配置示例使用 primary 65536、vision 32768，均不超过本次读取的服务声明。健康与目录响应不代表生成质量验收。

`config/factory.wkp.example.json` 是控制层配置格式示例；替换已有授权 TLS 文件路径。文本创作与独立审稿均走 primary，视觉质检走 vision。不会读取或复制 Wkp 原始模型凭据。

图像连接选择 `comfyui_qwen_image21`，模型为 `Qwen-Image-2.1`，经 `production.gpu_service` 向 8791 提交 `kind=qwen_image21`，服务内调用 8352。请求只含提示词、seed、尺寸、步数和至多4张有哈希的参考图，不发送任意 ComfyUI 图。任务 ID 在网络提交前落盘，恢复复用同一任务，验证产物哈希及模型回执。图像尺寸须为32倍数，范围256–1536。

视频沿用已验证的 `comfyui_h3` 图和自动提示词，通过 8791 执行。当前服务声明 turbo4 配方；示例沿用该配方，不把20步正式档当成已部署能力。Wkp 可用 `production.resident_h3` 指定常驻8346服务及GPU6、7，避免启动另一实例。`config/wkp-inference.example.json` 仅列服务端增量设置，需合并已有私有配置，不是可直接覆盖现有配置的完整文件。

Qwen3-TTS 仍由 Wkp 任务服务按需启动进程，生成卡配置为GPU5；常驻图像GPU4与文本/视觉GPU0–3不进入TTS调度。剪辑继续在现有业务执行机运行。

服务端 Qwen Image 适配器根据用户已有部署源码整理纳入仓库；本次没有修改服务器文件或重启服务。生成请求与私有配置不进入 Git。公开示例不会自动改写正在运行的配置。真实生成效果仍需单独记录，与CPU/协议模拟测试分开。

# 部署、升级与恢复

## 部署边界

当前两台服务器部署：cami 运行完整控制层、工作流 worker、SQLite 和剪辑；Wkp 仅提供模型/GPU 推理 API。cami 不安装 GPU 模型、不启动本机推理进程。部署步骤见 [cami 控制层部署](CAMI_CONTROL_PLANE.md)。以下本机 GPU 步骤仅适用于推理服务器或单机安装。

控制层是一个 Python Web 服务和一个持久化 worker，共用 SQLite。LLM 以外置服务的方式连接；图像、视频和配音各自使用独立推理环境。界面使用 React + TypeScript + Vite，在构建机生成 `ui/factory/dist` 静态资源，由 Python 同源提供。运行服务器无需 Node 服务。

HTTP 管理面仅支持 loopback 绑定；配置非 loopback host 会在数据库初始化之前被拒绝。Factory、共享媒体和模型管理路由采用共同 Host/Origin 边界；这不代替公网认证。远程访问、代理限制、符号链接媒体兼容性和任务暂停/取消契约见 [HTTP 安全审查表](HTTP_SECURITY.md)。数据库沿用 SQLite 表结构；新项目保存 `workflow_version=2`，缺少该字段的已有项目按 v1 检查点恢复。

建议使用 Linux、Python 3.12，以及支持对应 CUDA/PyTorch/模型组合的 NVIDIA GPU。控制层本身不要求 GPU。多张卡是可调度资源，不代表每个视频模型会自动使用全部显卡。

## 1. 安装控制层

```bash
git clone https://github.com/Xiiiing/ai-studio.git
cd ai-studio
python3.12 scripts/bootstrap.py
```

脚本创建 `envs/factory` 并安装 `requirements-factory.txt`，再执行 `npm ci --ignore-scripts` 和 `npm run build`；构建机需要 Node.js 24。已有环境和 live 配置会保留。若没有原来的媒体环境，建立指向控制环境的 `.venv` 链接供媒体辅助命令使用。它不会下载模型、改动 NVIDIA 驱动或启动服务。

在开发机或 CI 构建 UI 后，可将 `ui/factory/dist` 随对应代码提交的发布包传到运行服务器，并运行 `scripts/bootstrap.py --skip-ui-build`。不要从生产服务器复制私有配置到构建机。源码 checkout 没有已构建 UI 时，主页返回带构建说明的 503。

仅初始化示例配置：

```bash
python3.12 scripts/bootstrap.py --skip-install
```

`config/factory.example.json` 的 writer/critic 是服务别名示例。先按[模型接口说明](MODEL_PROVIDERS.md)启动服务、修改模型 ID 和能力，再测试连接。

## 2. 制作依赖

### ComfyUI

当前适配器使用 ComfyUI 的本地 HTTP API。按[官方部署说明](https://github.com/Comfy-Org/ComfyUI)准备独立目录及该目录下的 `.venv/bin/python`。

本项目集成验证使用的 ComfyUI commit 为 `9a9fdb10ed144ce760d9682cb247526ea23cc525`。更新 ComfyUI 后，应验证启动参数、节点类型、工作流 schema 和输出文件结构；不能只以 HTTP 端口可达作为生成验收。

默认查找本仓库相邻的 `ComfyUI` 目录，其他位置通过环境变量指定：

```bash
export DRAMA_COMFY_HOME=/srv/ComfyUI
```

项目会按 GPU 租约创建自己的工作器、输入/输出目录和临时模型目录配置，使用 `nodes/` 下的本地节点；不会停止不属于它的其他模型进程。

图像和视频权重下载脚本保留来源 revision 与校验：

```bash
envs/factory/bin/python scripts/download_models.py --family image
envs/factory/bin/python scripts/download_models.py --family video
envs/factory/bin/python scripts/download_fastwan.py
```

这些命令会下载较大的模型文件，只在需要对应引擎时运行。权重写入 `models/`，不进入 Git。模型许可、显存和推理参数需按所用模型确认。

### 配音

Qwen3-TTS 使用独立的 `envs/tts`。仓库的 `requirements-tts.txt` 记录一套 CUDA 12.1 / PyTorch 2.5.1 的安装组合；不同驱动和 GPU 应使用兼容组合，不与控制层环境混装。

对应组合的安装顺序：

```bash
python3.12 -m venv envs/tts
envs/tts/bin/python -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
envs/tts/bin/python -m pip install -r requirements-tts.txt
envs/tts/bin/python audio/download_model.py
```

`audio/voices.json` 保存角色默认声线。当前默认动态漫使用画外音；口型同步不是这条默认路线的依赖。旧口型适配器在源码中保留，但需要单独准备其环境、上游代码和权重，不能仅凭控制层安装完成就认为可运行。

### 中文字幕与封面字体

Linux 需要安装中文字体。Debian / Ubuntu 可安装系统提供的 Noto CJK 字体，再确认字体可被找到：

```bash
sudo apt-get install fonts-noto-cjk
fc-match 'Noto Sans CJK SC'
```

也可自行准备获准使用的 `assets/fonts/NotoSansCJKsc-Regular.otf`。项目优先使用该字体，字幕渲染器还会读取系统字体。字体不随本仓库打包；自行复制字体时应保留其许可证。缺少中文字体可能导致封面或字幕显示方块，应在首条样片中检查。

## 3. 配置 GPU 和预算

在本机私有 `config/factory.json` 中编辑：

- `production.gpu_cards`：允许制作使用的物理卡编号。
- `max_parallel`、`max_gpu_workers`：默认均为 1。
- `image_min_free_mib`、`gpu_min_free_mib`：开始工作前需要的空闲显存。
- 项目的 `gpu_minutes`、`wall_minutes`、`max_attempts`：本次制作上限。

常驻 LLM 使用的 GPU 应从生成卡候选中排除，或由显存检查阻止冲突。升级部署会保留已有共享 GPU 锁目录；新安装默认使用自身 `runtime/gpu_locks`。多个协作进程可显式统一 `DRAMA_GPU_LOCK_DIR`。

GPU 快照只反映采样时刻，不构成长期资源预留。执行器还会获取租约并再次检查资源，忙碌时等待或暂停。

## 4. 长期运行

先用前台方式确认配置，然后安装这两个项目服务：

```bash
sudo envs/factory/bin/python scripts/factory_service.py install
sudo envs/factory/bin/python scripts/factory_service.py start
```

安装器按仓库的真实路径渲染 `deploy/` 模板；默认以目录所有者运行，也可在安装时使用 `--user USERNAME`。目录和运行时文件必须可由该用户读写。为避免 systemd 路径歧义，安装路径不应包含空白或特殊转义字符。

服务名称：

- `drama-factory-web.service`
- `drama-factory-worker.service`

LLM 服务自行管理，例如用对应部署的 systemd 单元；更改工作台不会自动重启 LLM。

状态与日志：

```bash
envs/factory/bin/python scripts/factory_service.py status
journalctl -u drama-factory-web -n 80
journalctl -u drama-factory-worker -n 80
```

### 环境变量和密钥

将 `config/runtime.env.example` 复制为 `config/runtime.env`，只填写实际需要的变量，权限设为 600。两个 systemd 单元均读取此文件。文件被 Git 忽略，配置与 API 只引用密钥变量名。

环境文件修改后需要重启使用它的服务；普通模型目录保存不需要重启。不要将真实密钥写入 JSON、命令行参数、Git 提交或共享截图。

### 访问界面

默认监听 `127.0.0.1:8787`。远程服务器可在个人电脑运行：

```bash
ssh -N -L 8787:127.0.0.1:8787 your-user@your-server
```

然后打开 http://127.0.0.1:8787/。SSH 转发是访问方式，不会让浏览器直接连接模型服务。

工作台目前定位为单用户本地管理界面，没有互联网多租户认证层。需要公网访问时，先配置认证代理和符合本机源策略的转发；不要简单改为监听所有网卡。

## 5. 升级和数据

源码和数据分开：

| 路径 | 内容 | Git |
| --- | --- | --- |
| `factory/`、`studio/`、`ui/` | 程序与界面 | 提交 |
| `config/*.example.*` | 可公开配置示例 | 提交 |
| `config/factory.json`、`config/runtime.env` | 本机设置与凭据环境 | 不提交 |
| `runtime/factory/` | 数据库、检查点、worker 状态 | 不提交 |
| `production/` | 项目、素材、成片与生成回执 | 不提交 |
| `models/`、`envs/` | 模型权重与运行环境 | 不提交 |

升级前记录当前提交并备份配置、SQLite 与生产工程。数据库备份使用 SQLite backup API 或在相关服务停止后进行，不要在有写入时仅复制一个 WAL 数据库文件。

先确认队列没有运行中的生成任务，再更新源码、检查依赖并重启两个控制服务。若有任务，先通过工作台请求暂停，等待其到达安全边界。成功素材与决策版本应保留，不因更新代码而清空。

回退程序时恢复先前源码版本和相匹配的配置，保持模型服务与素材数据；涉及数据库 schema 变化时需要按对应版本迁移说明处理。本版保留旧执行图读取能力，工作流版本固定到项目；不要将已有 LangGraph 检查点直接套用新版节点。回退时不得覆盖试用期间的新决策、费用账本和素材。

## 6. 测试

```bash
envs/factory/bin/python -m pytest -q tests/test_factory_*.py
npm ci --ignore-scripts
npm test
npm run build
python scripts/browser_fixtures.py
FACTORY_TEST_PYTHON=envs/factory/bin/python npm run test:browser
```

Node 用于 UI 构建和开发测试，运行已构建工作台不需要它。CPU/协议测试不代表真实 GPU 视频质量验收；新模型接入后应做小规模结构化文本、视觉输入和镜头生成验证，并检查实际输出回执。

### 首次配置与 API 用户

初始化使用 `config/factory.empty.example.json`，保留旧安装的配置。普通 API 用户不需要安装文本模型；在工作台添加平台预设并配置私有密钥环境变量。`localhost` 始终指运行后端的机器。前台 Web、worker 和 CLI 也会读取权限为 600 的 `config/runtime.env`；已有进程环境优先，修改后重启。示例 `config/factory.example.json` 保留作为完整本地配置参考，不会自动作为新安装的已就绪连接。

### 无 GPU 与混合制作

普通 API 用户按[用户模型接入指南](USER_MODELS.md)配置图像、配音和视频能力，控制服务器无需安装模型或 CUDA。只有选择本机管理的制作后端才初始化 GPU 调度和检查相应本地依赖。外部 ComfyUI 使用 HTTP 上传／下载；TTS 既可使用自己的本地环境，也可选择已适配的云端语音 API。

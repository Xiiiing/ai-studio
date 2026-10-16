# Getting started and creative operations

[Project home](../README.en.md) · [Documentation](README.en.md) · [简体中文](GETTING_STARTED.md)

This guide starts with empty configuration and your own models or API accounts. The control layer does not include model weights or require access to the developer's servers. The application UI is Chinese; this guide translates the main labels. Detailed linked reference documents are currently Chinese.

## Install and start

Prepare Linux, Python 3.12 and Node.js 24 for the UI build, matching CI. The control layer does not require a GPU. Local generation needs compatible model environments; media encoding and Chinese subtitle fonts need the dependencies described in [deployment](DEPLOYMENT.md).

```bash
git clone https://github.com/Xiiiing/ai-studio.git
cd ai-studio
python3.12 scripts/bootstrap.py
./drama models
./drama workflow --json
```

Bootstrap creates `envs/factory`, installs control dependencies and builds `ui/factory/dist`. It initializes `config/factory.json` from `config/factory.empty.example.json` only when missing, preserving existing configuration. No model is enabled in a fresh installation. `config/factory.example.json` shows additional configuration fields; `config/factory.api.example.json` illustrates an all-API route. Replace example model aliases with actual IDs.

If matching prebuilt UI assets are already installed, `python3.12 scripts/bootstrap.py --skip-ui-build` skips the Node build. It does not supply missing assets. `--skip-install` only initializes configuration.

Run in separate terminals:

```bash
# Workbench and API
envs/factory/bin/python -m factory.web
```

```bash
# Background worker
envs/factory/bin/python -m factory.worker
```

Open [http://127.0.0.1:8787/](http://127.0.0.1:8787/). Both processes must run for production to advance. For persistent services and SSH forwarding, see [deployment](DEPLOYMENT.md).

## Choose a production route

| Route | What you supply |
| --- | --- |
| Cloud APIs | Supported text/vision providers, Bailian images and speech, and MiniMax video; accounts, model access and network connectivity. No local GPU is required. |
| Self-hosted | A text server such as llama.cpp, Ollama, vLLM or LM Studio; compatible ComfyUI workflows for images/video; a separate Qwen3-TTS environment for local speech. |
| Mixed | Choose a connection for each stage independently. `localhost` always means the backend machine. |

For API credentials, create a private environment file if it does not already exist:

```bash
cp config/runtime.env.example config/runtime.env
chmod 600 config/runtime.env
```

Edit an existing file instead of overwriting it. Fill in only the keys you need. Configuration stores the `api_key_env` variable name, not the secret. Web, worker and CLI read the file; existing process environment variables take precedence. Restart affected processes after changing it. Provider regions, workspaces and paid-batch recovery are covered in the [provider guide](USER_MODELS.md).

## Configure and check connections

Open “模型与连接” (Models and connections):

1. Add or edit a connection with its provider, endpoint, actual model ID, context size and capabilities.
2. Assign models to roles. “视觉质检” (Visual critic) must use a vision-capable configuration.
3. Click “保存配置” (Save configuration). Saving does not start production, install weights or launch arbitrary model services.
4. For saved text connections, click “测试连接” (Test connection) to check discovery and model IDs. Fix, save and retest failed configurations. Unsaved drafts cannot be probed.
5. Configure image, speech and video connections for the selected route. “预检配置” (Check configuration) checks settings and credential references; it does not generate media.
6. In “制作工作流” (Production workflow) or “团队与回执” (Team and receipts), inspect role inputs, artifacts and execution history. Current assignments and historical models are shown separately.

Base URLs are complete API prefixes; `/v1` is not appended universally. For example, Bailian retains `/compatible-mode/v1`, while native Ollama uses the server root or `/api`. Discovery can be explicitly skipped for supported configurations, but that check remains unverified. Actual calls still validate response identity and structure. The MiniMax adapter implements the supported v1 image-to-video protocol, not every video provider or protocol variant. See [model providers](MODEL_PROVIDERS.md).

CLI equivalents, replacing profile placeholders with your saved IDs:

```bash
./drama roles
./drama models probe YOUR_TEXT_PROFILE
./drama models probe YOUR_VIDEO_PROFILE --kind video
./drama models export --file runtime/models-edit.json
# Edit llm_profiles and llm_roles in the exported file.
./drama models apply --file runtime/models-edit.json
```

Exports retain a configuration revision. If another window has saved changes, export again and reconcile them before applying; stale files are rejected.

## Workspace 0.0.2: themes, navigation and drafts

The top-bar **外观主题** control switches between light, dark and system appearance, including changes to the operating-system preference. On a phone, use the upper-left button for studio navigation and the current project page name to select any of the ten project pages. Selecting a stage opens its steps, and desktop step details scroll independently. The project search filters the full list before applying pagination.

Model settings and shot-edit drafts stay in memory for the **current application session** across page or shot navigation; saves lock the corresponding edit until completion and preserve unsaved changes after errors. Drafts are not stored in browser persistent storage. Refreshing or leaving warns about unsaved work; if the server revision changes, inspect the newer version before submitting. See the [0.0.2 UX validation report](UI_REDESIGN_0_0_2.md) (Chinese).

## Read workflow artifacts

The project overview shows five stages: story, script/storyboard, visuals, shots/post-production, and final delivery. Select a stage to inspect its actual steps, inputs, artifacts, roles and execution records. On narrow screens, details appear below the graph with a return action. Paused and human-waiting states stop activity animation; reduced-motion settings disable it. Refresh preserves your selected step and open document.

“阅读文件” (Read file) opens Markdown, JSON or media. Text previews have a 2 MiB limit; larger files can be downloaded. Failed reads can be retried. Execution records preserve saved requests, responses, parsed results, prompts and retries. Old calls without step attribution are shown as role history rather than assigned to a guessed step. Reading artifacts does not start generation or change decisions.

## Submit a project and handle review

Configure models before submitting:

```bash
./drama run \
  --brief "An original neighborhood mystery with two adult characters and few locations. Open with a hook, resolve the twist, and use motion-comic visuals with voice-over." \
  --title "Neighborhood mystery" \
  --format motion_comic \
  --seconds 60 \
  --gpu-minutes 120 \
  --wall-minutes 180 \
  --max-attempts 3 \
  --request-id first-episode
```

The executive producer agent selects eligible story and visual directions by default. If it is not configured, the workflow asks for human input. Add `--team-mode guided` when creating a project to select stories and styles yourself.

Review actions offer all recommendations, specific issues and common directions. You may add custom notes. Visual regeneration and final-cut revision use similar options. Revision focus does not bypass independent review. Unsaved selections survive refresh; revision conflicts require reconciling the latest state.

Creative review defaults to four rounds per batch, including three automatic revisions. After reaching the limit, a configured executive producer can analyze a repair plan, reasons and tradeoffs. You can request “请 AI 分析并推荐” (Ask AI to analyze and recommend), accept a bounded follow-up batch or submit your own direction. Final approval, budget increases and new paid batches remain your decisions. `creative.max_review_rounds` supports 1–6; explicit older limits remain valid.

```bash
./drama status PROJECT_ID
./drama choose PROJECT_ID --type story --choice PROPOSAL_ID --decision-id STORY_GATE_ID
./drama choose PROJECT_ID --type style --choice STYLE_ID --decision-id STYLE_GATE_ID
./drama outputs PROJECT_ID
```

Replace all uppercase placeholders with actual IDs from status output. Request pause in the workbench and wait for a safe boundary before resuming or performing maintenance. Investigate failed steps and receipts before retrying an unknown paid request. Pause does not immediately kill a model process.

The first cut and approved delivery are separate states. Review story completeness, continuity, shot quality, speech, subtitles, editing and cost records before approving. Targeted repair and re-editing preserve applicable completed work. Project artifacts and receipts remain under `production/`, outside Git.

## Troubleshooting

| Symptom | Check first |
| --- | --- |
| Home returns 503 | Build `ui/factory/dist` and ensure it matches the source checkout. |
| Project does not advance | Confirm the worker runs, role assignments are complete, and no human or paid-batch decision is pending. |
| Configuration passes but generation fails | Preflight is not inference validation. Check actual IDs, capabilities, schema, region, permissions and receipts. |
| Remote connection fails | Check reachability from the backend, exact allowed hosts, network scope and HTTPS for authenticated remote connections. |
| Chinese subtitles show boxes | Install suitable fonts on the production machine; see [font dependencies](DEPLOYMENT.md#中文字幕与封面字体). |
| Configuration revision conflict | Reload or export the latest configuration, reconcile changes and save again. |

Continue with the [architecture guide](ARCHITECTURE.md) and [API reference](API.md), both currently Chinese.

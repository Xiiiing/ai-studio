# 贡献指南 / Contributing

[中文首页](README.md) · [English home](README.en.md) · [文档中心](docs/README.md) · [Architecture](docs/ARCHITECTURE.md)

欢迎改进 AI Studio 的创作流程、模型适配、工作台、文档与验证。提交前先查找已有 [Issue](https://github.com/Xiiiing/ai-studio/issues) 和 [PR](https://github.com/Xiiiing/ai-studio/pulls)，说明要解决的具体问题与预期行为；较大改动先讨论设计与兼容边界。

Contributions to workflows, providers, UI, documentation and validation are welcome. Check existing issues and pull requests first. Describe the problem, expected behavior and compatibility requirements; discuss substantial changes before implementing them.

## 开发准备 / Development setup

使用 Linux、Python 3.12 和 Node.js 24（与 CI 一致）。以下命令只建立控制层开发环境，不安装模型或读取生产数据：

Use Linux, Python 3.12 and Node.js 24, matching CI. These commands prepare control-layer development without installing models or reading production data:

```bash
git clone https://github.com/Xiiiing/ai-studio.git
cd ai-studio
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-factory.txt -r requirements-evaluation.txt
npm ci --ignore-scripts
npm run build
```

需要启动工作台时，按[首次使用](docs/GETTING_STARTED.md)或 [English guide](docs/GETTING_STARTED.en.md) 初始化配置并运行 Web 与 worker。开发测试使用独立临时数据库和 fixtures；不要把生产 `runtime/`、`production/` 或私有配置复制进测试。

To start the application, follow the getting-started guide. Tests use isolated databases and fixtures; do not copy production runtime, media or private configuration into tests.

## 变更与提交 / Changes and pull requests

- 一个 PR 解决一个完整且可验证的问题；相关实现、有效测试与文档一起提交。描述触发条件、最终行为、兼容性和实际验证。
- 行为变化应补充有效测试；模型协议变化更新示例配置与 [MODEL_PROVIDERS.md](docs/MODEL_PROVIDERS.md)，HTTP 变化更新 [API.md](docs/API.md)。
- UI 变化检查适用的桌面和手机操作，并提供明确来源的截图；区分模拟界面、真实 HTTP 与真实推理证据。
- 保存和查看不应隐式启动生成；保持项目版本、任务恢复、费用授权和模型身份的既有契约。
- commit 说明修改目的。PR 关联 Issue，只有完整解决时使用 `Closes #编号`；报告未执行或受阻的检查。

Keep each PR focused on a complete, verifiable outcome. Include relevant implementation, meaningful tests and documentation. Preserve versioning, recovery, paid-batch authorization and model-identity contracts. UI changes need applicable desktop/mobile checks and screenshots with provenance. Link the issue and describe actual validation; use `Closes #...` only for complete solutions.

## 本地验证 / Local validation

以 [CI 配置](.github/workflows/ci.yml)和 [package.json](package.json) 为实际入口，按修改范围运行：

Use the CI workflow and package scripts as the source of truth. Select checks relevant to your changes:

```bash
# CPU workflow/provider contracts
.venv/bin/python -m pytest -q tests/test_factory_*.py

# React component and backend payload contracts
FACTORY_TEST_PYTHON=.venv/bin/python npm test

# Type checking, production UI and bundled dependency notices
npm run build

# CPU-only synthetic media and Chromium browser checks
.venv/bin/python scripts/browser_fixtures.py
npx playwright install chromium
FACTORY_TEST_PYTHON=.venv/bin/python npm run test:browser
```

Linux 缺少浏览器系统依赖时，可按 CI 使用 `npx playwright install --with-deps chromium`。浏览器测试使用本机回环 fixture 服务；常规截图和报告写入被忽略的 `runtime/browser-report/`。测试入口说明见 [tests/ui/README.md](tests/ui/README.md)。

On Linux, use the CI command `npx playwright install --with-deps chromium` if browser system dependencies are missing. Browser fixtures bind to loopback. Routine screenshots and reports stay in the ignored `runtime/browser-report/` directory.

CI 在 `main`、`codex/**` 分支推送和 PR 时运行，开发分支无需提前发布 PR 即可验证。`python-contracts` 运行完整 Python 契约；`cpu-and-ui` 运行 UI 单测、类型检查、构建和完整浏览器回归。两个任务独立执行，各有 15 分钟上限，验收时须同时通过。浏览器 fixture 共享可重置状态，继续使用一个 worker、零自动重试；入口调整时更新实际操作路径，保留原有行为和几何断言。

CI runs on pushes to `main` and `codex/**`, and on pull requests, so development branches can be checked before publishing a PR. The independent `python-contracts` and `cpu-and-ui` jobs each have a 15-minute limit; both must pass. The browser suite keeps one worker and zero retries because its resettable fixture state is shared. Adapt changed entry paths while retaining behavioral and geometry assertions.

通过这些检查证明的是软件行为，不是供应商在线可用性或生成艺术质量；真实模型联调需明确授权和对应证据。纯文档修改核对事实、链接、命令、示例、引用和渲染即可；展示截图需要与所说明的源码一致。

These checks validate software behavior, not provider availability or artistic quality. Real-model validation needs authorization and separate evidence. Documentation-only changes need factual, link, command, example, citation and rendering checks; screenshot provenance must match its source build.

## 文件与数据边界 / Repository boundaries

| 提交 / Commit | 留在本机 / Keep private or generated |
| --- | --- |
| 源码、测试、公开示例、文档 / Source, tests, public examples, docs | 密钥、私有配置 / Secrets and private configuration |
| 来源明确的必要展示资源 / Curated assets with provenance | 数据库、生成素材、模型权重、环境 / Databases, generated media, weights, environments |
| `config/*.example.*` | `config/factory.json`, `config/runtime.env`, `.env*` |
| [`docs/assets/`](docs/assets/README.md) 中的精选仓库截图 / Curated repository previews | `runtime/`, `production/`, `models/`, `envs/`, `.venv/`, `node_modules/` |

提交前核对暂存差异和 [.gitignore](.gitignore)。不要在 Issue、日志或截图公开凭据、私有地址中的密钥或生产素材。第三方代码和素材必须记录准确来源与许可，不从模型许可推断源码许可，也不反向推断。

Inspect staged diffs and ignore rules before committing. Never include credentials or private production content in issues, logs or screenshots. Record sources and terms for third-party code and assets. Model and source-code licenses are independent.

## 文档与品牌维护 / Documentation and brand maintenance

中文首页为 [README.md](README.md)，英文首页为 [README.en.md](README.en.md)。能力、命令、边界和重要链接应同步更新；技术参考保留一种主要语言时，在英文入口标明。新文档加入中英文[文档中心](docs/README.md)，保留被外部引用的既有路径。历史记录保留日期、基线与测试范围。

Keep the Chinese and English homepages consistent in capabilities, commands, limits and links. Label untranslated references in English navigation. Add new documents to both indexes and preserve externally referenced paths. Historical evidence retains its dates, baseline and scope.

品牌 SVG 和精选截图的来源、尺寸与复现方法见[资源说明](docs/assets/README.md)。截图使用隔离 fixtures，更新时检查没有私人数据，不把模拟结果称为实际生成。项目引用使用 [CITATION.cff](CITATION.cff)；只有真实发布才填写发布版本与日期。

See the asset guide for SVG artwork and curated screenshot provenance. Use isolated fixtures for repository previews. Citation metadata belongs in `CITATION.cff`; only add release versions and dates for actual releases.

本仓库当前未指定统一源码许可证；贡献与使用需尊重现有权利及[第三方条款](THIRD_PARTY_NOTICES.md)。

The repository currently has no unified source-code license. Respect existing rights and third-party terms when contributing or using it.

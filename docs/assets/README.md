# 品牌与展示资源 / Brand and presentation assets

[文档中心 / Documentation](../README.md) · [项目首页 / Project home](../../README.md)

| 文件 / File | 用途 / Purpose |
| --- | --- |
| [logo.svg](logo.svg) | 原创电影画框、播放符号与创作星芒；128 × 128，可缩放。 / Original film frame, play symbol and creative spark; scalable 128 × 128. |
| [banner.svg](banner.svg) | README 横幅；1440 × 440，内置深色背景，在浅色和深色页面均可读。 / README banner with its own dark background. |
| [workbench.png](workbench.png) | 主分支工作台的浏览器截图；1440 × 1000。 / Browser capture of the workbench on the base commit. |
| [workflow.png](workflow.png) | 五阶段流程和步骤详情的浏览器截图；1440 × 1000。 / Browser capture of the five-stage workflow and step details. |

## 来源与复现 / Provenance and reproduction

Logo 与横幅由本项目以 SVG 路径、几何图形和系统字体绘制，未使用第三方品牌、图片或打包字体。SVG 包含可访问标题与说明，不包含脚本或外部资源。品牌资产沿用项目当前的许可状态；没有另行授予商标或素材许可。

`logo.svg`同时用于 README、工作台侧栏和浏览器图标。前端静态副本为`ui/factory/public/brand.svg`，保留完整图形与原色，品牌单测校验两文件逐字一致。修改 Logo 后执行`cp docs/assets/logo.svg ui/factory/public/brand.svg`同步；构建会将静态副本复制到前端产物，无需运行时读取文档目录。

截图来自基线 `0b936b0` 的真实 React 生产构建，使用 `tests/browser/server.mjs`、`tests/browser/console-fixture.mjs` 和 `scripts/browser_fixtures.py` 中的隔离模拟数据。主页面使用 `automatic` 场景，项目流程使用同一场景的 `overview` 标签。它们展示界面与导航，**不是生产项目、真实模型输出或成片质量证据**；没有连接 Wkp、读取生产数据库或调用付费 API。界面当前为中文；英文 README 不代表应用已提供英文界面。

SVG artwork is original to this repository and contains no scripts, embedded fonts or external resources. Screenshots show the actual React production build at base commit `0b936b0`, populated by isolated browser fixtures in the `automatic` scenario. They demonstrate the interface, not real inference or production quality. The application UI shown is Chinese.

复现准备 / Prepare:

```bash
npm ci --ignore-scripts
npm run build
python scripts/browser_fixtures.py
TEST_PORT=18789 node tests/browser/server.mjs
```

在 Chromium 中打开 `http://127.0.0.1:18789/`。先向 `/__test/scenario` POST `{"kind":"automatic"}`，再以 1440 × 1000 视口截取首页，以及 `/#project=012345abcdef&tab=overview` 的项目流程。仅使用本机回环地址。更新截图时同时更新基线说明，检查没有私人数据，再提交这两张用于仓库展示的 PNG。常规测试截图仍留在被忽略的 `runtime/` 中。

Use Chromium at 1440 × 1000. POST `{"kind":"automatic"}` to `/__test/scenario`, then capture `/` and `/#project=012345abcdef&tab=overview`. Update the documented base commit whenever replacing these curated images. Routine test evidence stays in the ignored `runtime/` directory.

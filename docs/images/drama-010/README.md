# 整剧与长集界面证据

这些图片由`tests/browser/dramas.spec.mjs`与`tests/browser/drama-navigation.spec.mjs`在隔离的浏览器模拟API上生成，不访问生产数据，不表示真实模型生成质量。

- `episodes-1440.png`、`episodes-390.png`：勾选第1、3集、提交批次、暂停批次与剧集列表。
- `long-episode-1440.png`、`long-episode-390.png`：长集观看、单元进度与指定单元返修。播放器使用CPU生成的短测试视频，页面900秒为模拟业务元数据。

- `workflow-1440.png`、`workflow-390.png`：未确认规划时独立切换工作区，展开编剧与审稿任务，查看输入、输出与问题。使用模拟回执，不表示真实模型生成结果。

复现：安装现有项目依赖，运行`python scripts/browser_fixtures.py`、`npm run build`、`npm run test:browser -- tests/browser/dramas.spec.mjs`。自动断言手机无水平溢出，并实际播放测试媒体、提交选集及返修请求。

页签与导航回归：`npm run test:browser -- tests/browser/drama-navigation.spec.mjs`，覆盖整剧总览 → 长集流程 → 单元团队工作台，以及刷新和浏览器后退。

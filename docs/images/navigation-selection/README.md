# 导航与卡片选中态

模型入口统一到“系统设置 → 模型与服务”。导航使用整块柔和底色与文字/图标强调，设置选项使用完整细描边和勾选；不使用左侧竖条，键盘焦点使用独立外轮廓。

以下为生产构建配合隔离 API fixtures 的浏览器截图，不含真实模型调用或生产数据。桌面 1440px 和手机 390px、深浅主题下，对六款主题逐一检查文字对比度、选中标记、悬停几何稳定、键盘焦点与只读请求；近期项目、镜头和工作流选择另作实际操作检查。

| 视图 | 浅色 | 深色 |
| --- | --- | --- |
| 桌面系统设置 | [截图](settings-light-1440.png) | [截图](settings-dark-1440.png) |
| 手机系统设置 | [截图](settings-light-390.png) | [截图](settings-dark-390.png) |
| 手机导航 | [截图](navigation-light-390.png) | [截图](navigation-dark-390.png) |

复现：`npm run build` 后运行 `npm run test:browser -- tests/browser/selection-style.spec.mjs`。旧模型链接、刷新与浏览器前进后退由 `flat-navigation.spec.mjs` 覆盖；模型保存行为继续由系统设置与模型浏览器测试验证。

# 导航与卡片选中态

模型入口统一到“系统设置 → 模型与服务”。主导航保持黑白选中；第二级设置分类、最近制作和其它原带左侧竖条的卡片改为中性底色与完整细边界，设置分类与最近制作增加字重。设置分类和主题色卡不显示选中小勾。键盘焦点使用独立外轮廓。

以下为生产构建配合隔离 API fixtures 的浏览器截图，不含真实模型调用或生产数据。桌面 1440px 和手机 390px、深浅主题下，对六款主题逐一检查主导航黑白色不随主题改变、第二级分类、外观选项与最近制作选中态、悬停几何稳定、键盘焦点与只读请求。

| 视图 | 浅色 | 深色 |
| --- | --- | --- |
| 桌面系统设置 | [截图](settings-light-1440.png) | [截图](settings-dark-1440.png) |
| 手机系统设置 | [截图](settings-light-390.png) | [截图](settings-dark-390.png) |
| 手机导航 | [截图](navigation-light-390.png) | [截图](navigation-dark-390.png) |

桌面外框保持固定，只滚动右侧内容：

| 位置 | 浅色 | 深色 |
| --- | --- | --- |
| 顶部 | [截图](frame-top-light.png) | [截图](frame-top-dark.png) |
| 内容底部 | [截图](frame-bottom-light.png) | [截图](frame-bottom-dark.png) |

`sidebar-scroll.spec.mjs`覆盖外框、侧栏位置与视口底部边距、长列表独立滚动、弹窗滚动锁定/恢复、路由复位以及窗口高度变化。

复现：`npm run build` 后运行 `npm run test:browser -- tests/browser/selection-style.spec.mjs`。旧模型链接、刷新与浏览器前进后退由 `flat-navigation.spec.mjs` 覆盖；模型保存行为继续由系统设置与模型浏览器测试验证。

`recent-{light|dark}-{1440|390}.png` 展示最近制作的完整选中边界，无左侧竖条或选中小勾。

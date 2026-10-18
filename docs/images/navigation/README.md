# 扁平导航与总览待办

`flat-navigation-1440.png`展示桌面五项主导航和总览中的待办；`flat-navigation-390.png`展示手机导航抽屉。两图均由`tests/browser/flat-navigation.spec.mjs`使用隔离模拟API和实际生产构建生成，不是生产项目或模型生成证据。

主导航仅保留总览、项目、素材库、运行记录、模型配置。外观设置从右上角页面工具进入，具体项目的流程和团队仍在项目中查看。浏览器检查100项以上待办翻页、失败重试、旧项目准确跳转、旧全局链接及外观偏好保持。

复现：`npm run build`后执行`npm run test:browser -- tests/browser/flat-navigation.spec.mjs`。设计约定见[界面设计](../../UI_DESIGN.md#导航与内容)。

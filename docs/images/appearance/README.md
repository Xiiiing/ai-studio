# 外观设置证据

`settings-light-1440.png`、`settings-dark-1440.png`为桌面设置页，`settings-light-390.png`、`settings-dark-390.png`为手机设置页。展示六款主题色、独立显示模式和杏桃效果预览。

截图由`tests/browser/appearance-settings.spec.mjs`使用真实生产构建与隔离模拟API生成。浏览器实际点选全部六款配色，检查明暗主题、灰阶业务卡底色稳定、主题细边变化、主操作及阅读区保持黑白、中性与红黄绿语义色稳定、跨页面保持、刷新恢复、跨标签同步、键盘焦点、320px布局、首屏脚本，以及API不可用时仍能切换。测试不调用模型或写入生产数据。

复现：`npm run build`后执行`npm run test:browser -- tests/browser/appearance-settings.spec.mjs`。配色来源与持久化范围见[界面设计](../../UI_DESIGN.md#外观设置)。

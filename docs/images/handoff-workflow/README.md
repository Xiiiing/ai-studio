# 部门和岗位交付连线

截图来自隔离模拟 API，使用合成的规划与审稿记录，不代表真实模型生成或用户验收。

- `delivery-1440.png`：桌面保留部门总图，在旁侧阅读交付内容、发出方和接收方。
- `delivery-390.png`：手机保留总图，用可收起的底部阅读区显示交付内容。

复现：`npm run build` 后运行 `npx playwright test tests/browser/drama-navigation.spec.mjs -g 'employee edges'`。同一检查还验证岗位连线、两条反向交接线均可点击，输入与输出归属、岗位当前状态、键盘操作、刷新恢复和文字不溢出节点。

自动验证涵盖历史调用无接收关联时的原文阅读；实际 HTTPS 现有项目只读验证另存于本机 runtime，不上传其项目资料。

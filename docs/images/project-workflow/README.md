# 项目导航与制作流程优化

截图为隔离模拟 API 的浏览器操作记录，不是模型生成或用户验收证据。

- `flow-dark-1440.png` / `flow-dark-390.png`：四个常用入口、完整流程、真实节点状态与返修返回路线。
- `node-light-1440.png` / `node-light-390.png`：节点材料与执行历史分层查看。
- `running.png`：模拟执行中的数据流；暂停或人工确认时停止，减少动态偏好时不播放。

复现：`npm run build` 后运行 `npx playwright test tests/browser/drama-navigation.spec.mjs tests/browser/workflow-reader.spec.mjs`，输出到 `runtime/browser-report/`。

交互参考：[n8n 节点输入](https://docs.n8n.io/data/data-mapping/data-mapping-ui/)与[执行历史](https://docs.n8n.io/workflows/executions/all-executions/)。本应用保持服务端阶段定义、职责与审核语义，只调整展示和查看顺序。

# 部门与岗位分层工作流

本目录为隔离模拟 API 的浏览器操作截图，不代表真实模型执行或用户验收。

- `departments-1440.png`、`departments-390.png`：项目部门、跨部门交接与逐层进入入口。
- `member-1440.png`、`member-390.png`：部门内部岗位、职责、实际工作和结果。

桌面为浅色、手机为深色；截图来自同一套岗位归属 fixture，编剧与审稿的记录分开呈现。

复现：`npm run build` 后运行 `npx playwright test tests/browser/drama-navigation.spec.mjs -g 'project departments'`。其他验证覆盖普通单元直达工作流、原始请求读取、历史负责人归属、暂停/阻塞/记录同步期间停用数据流。定义中的交接不代表发生过真实传输；未执行的岗位明确标注暂无记录。

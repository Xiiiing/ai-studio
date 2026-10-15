# React 工作台验收

`npm test` 使用 Vitest + Testing Library 渲染实际页面/组件，`npm run test:browser` 使用生产构建与 Chromium 验证浏览器行为。测试只使用隔离 fixtures；不连接 Wkp、生产数据库或生成模型。

旧原生 JS renderer 与七份 `test_factory*.mjs` 已随 React 重构移除。原测试的业务边界按下列入口迁移，避免继续测试用户无法访问的旧函数：

| 旧验收领域                                    | 当前验收入口                                                                |
| --------------------------------------------- | --------------------------------------------------------------------------- |
| 模型连接、角色绑定、保存冲突、服务协议        | `models.test.tsx`、`backend-contract.test.ts`、`browser/workbench.spec.mjs` |
| 故事/视觉/终片 gate、独立审核、输入保留、CAS  | `app.test.tsx`、`decisions.test.tsx`、`browser/console.spec.mjs`            |
| 工作流拓扑、成员、工单、产物交接与交付检查    | `workflow.test.tsx`、`browser/team.spec.mjs`                                |
| 单镜编辑、候选明确选择、草稿版本与播放连续性  | `assets.test.tsx`、`browser/workbench.spec.mjs`                             |
| Series Bible、素材版本与固定镜头引用          | `assets.test.tsx`，后端 `test_factory_series.py`                            |
| 付费授权、精确金额、费用确认与刷新竞态        | `assets.test.tsx`，后端 `test_factory_cloud_budget.py`                      |
| 创建规格、默认协作方式与原创/改编入口         | `app.test.tsx`，后端 `test_factory_profiles.py`、`test_factory_state.py`    |
| HTTP准入、旧接口退休、跨域拒绝、媒体Range播放 | `browser/http-boundary.spec.mjs`、后端 `test_factory_http_security.py`      |

`backend-contract.test.ts` 调用真实 Python provider normalizer；它只校验本地 payload，不探测网络或读取密钥。本地优先使用 `.venv/bin/python`，可用 `FACTORY_TEST_PYTHON` 显式指定隔离 Python 环境。

# 审核退回、配色与品牌证据

截图由 `tests/browser/review-return.spec.mjs` 在隔离模拟 API 上产生，不代表真实模型生成质量。

- `brand-light-1440.png`、`brand-dark-1440.png`：README 原有 Logo、白黑底色、低饱和深灰蓝主操作及审核退回记录。
- `brand-light-390.png`、`brand-dark-390.png`：手机导航中的同一 Logo、精简品牌名称及明暗主题。

复现：`npm run build` 后运行 `npm run test:browser -- tests/browser/review-return.spec.mjs tests/browser/ui-round3.spec.mjs`。检查 SVG 资源加载、统一图标引用、审核中到通过的显示、移动导航与横向溢出；颜色测试同时校验主按钮和语义状态 AA 对比度，以及大面板中性底色。

自动退回的真实编排由 Python 回归检查：`tests/test_factory_drama_review_return.py`、`tests/test_factory_continuity_return.py`、`tests/test_factory_continuity_video_authorization.py`。这些使用隔离 SQLite、真实缓存与合成媒体，模型输出为 mock，不访问生产模型。

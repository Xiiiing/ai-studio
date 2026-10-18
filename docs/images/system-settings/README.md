# 系统设置界面与验证

`tests/browser/system-settings.spec.mjs` 使用生产构建与隔离模拟 API 检查侧栏入口、外观显示、模型与服务、运行状态和关于与版本。截图覆盖 1440/390 宽度和浅深模式；320 宽度另做操作与溢出检查。图片中的版本、队列及模型是模拟数据，不代表真实服务或制作结果。

检查右上角调色板入口移除、主导航与分类选中、单一主标题、分类地址/刷新/历史、主题跨页保持、模型连接弹窗与同一配置保存、只读浏览零 API 写入、版本接口失败重试与未知信息回退。另以独立 Python HTTP 夹具验证真实系统信息接口。

复现：`npm run build` 后运行 `npm run test:browser -- tests/browser/system-settings.spec.mjs tests/browser/appearance-settings.spec.mjs tests/browser/flat-navigation.spec.mjs`。系统信息和 HTTP 边界测试使用 `tests/test_factory_system_info.py`、`tests/test_factory_web_models.py` 与 `tests/test_factory_http_security.py`。实际 cami 版本与服务检查另行记录，不据模拟截图推断部署版本。

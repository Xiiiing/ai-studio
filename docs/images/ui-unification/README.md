# 全站 UI 改造证据

所有截图来自独立浏览器 fixtures，使用模拟项目、色条视频和虚构模型；没有使用生产项目内容或凭据。改造前基线为 PR #50 的 `c05f2cb`，与当时已发布整合版本的 UI 相同。前后采用相同项目数据、视口与主题。

| 场景 | 改造前 | 改造后 |
| --- | --- | --- |
| 桌面 1440×1000 · 深色 | [查看](desktop-before.png) | [查看](desktop-after.png) |
| 手机 390×844 · 浅色 | [查看](mobile-before.png) | [查看](mobile-after.png) |

## 模型与连接

[桌面深色](desktop-models.png) · [手机浅色](mobile-models.png)

用途、模型身份与地址共同区分服务，保留连接、分工和高级工具；顶部主题只显示当前偏好图标。

## 镜头预览与审核

[桌面深色](desktop-shots.png) · [手机浅色](mobile-shots.png) · [双视频版本比较](versions-compare.png)

默认集中当前素材及审核，历史以紧凑列表呈现。比较是本地查看动作；选片、返修与生成仍须原有显式操作和确认。截图中的色条为测试媒体，不代表真实模型生成效果。

完整 7 个全局页面、10 个项目页面、两套主题及六种宽度的截图由 Playwright 生成在 Git 忽略的 `runtime/browser-report/redesign/`，CI 同时上传浏览器证据。

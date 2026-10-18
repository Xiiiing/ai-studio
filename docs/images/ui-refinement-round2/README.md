# UI 二轮对比证据

全部图片来自独立浏览器fixtures，项目、模型和审核记录均为模拟数据，视频为合成色条。没有生产项目内容、连接凭据或真实模型生成。

修改前为PR #52上一版 `2765018`，修改后为本轮实现；对比采用相同fixtures、系统浅色偏好、1000px视口高度及相同宽度。全页截图的总高度可因内容排版变化而不同。抽屉和通知采用视口截图，避免把屏幕外背景误认为浮层漏出。

| 场景 | 修改前 | 修改后 |
| --- | --- | --- |
| 镜头工作区，1440px | [查看](shots-1440-before.png) | [查看](shots-1440-after.png) |
| 镜头工作区，320px | [查看](shots-320-before.png) | [查看](shots-320-after.png) |
| 模型连接，1440px | [查看](models-1440-before.png) | [查看](models-1440-after.png) |
| 模型连接，320px | [查看](models-320-before.png) | [查看](models-320-after.png) |

[桌面概览](overview-1440.png) · [手机概览](overview-320.png)

[桌面状态抽屉](status-1440.png) · [手机状态抽屉](status-320.png)

[桌面请求失败](error-1440.png) · [手机请求失败](error-320.png)

全页面浅深主题六种宽度截图由完整浏览器回归保存在被Git忽略的 `runtime/browser-report/redesign/`，CI上传浏览器证据。上述截图展示视觉效果；布局稳定性、错误恢复、键盘操作和Logo尺寸另有实际操作断言，不仅凭截图判断通过。

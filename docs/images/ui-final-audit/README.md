# 全站 UI 自查及修复证据

截图来自独立 fixtures、合成媒体及临时数据库，不包含正式项目或凭据。修改前为 `823ae9f`，修改后为本次修复包 `CRmGsIl1 / CB3aPTac`；页头配对保持相同宽度、主题、有无成片状态，弹窗和错误配对沿用相同场景及视口。

| 问题 | 修改前 | 修改后 |
| --- | --- | --- |
| 桌面待成片操作区 | [1440px](header-1440-pending-before.png) | [1440px](header-1440-pending-after.png) |
| 手机待成片操作区 | [390px](header-390-pending-before.png) | [390px](header-390-pending-after.png) |
| 费用页多余空白 | [320px](costs-320-before.png) | [320px](costs-320-after.png) |
| 新建弹窗底栏裁切 | [320px](create-320-before.png) | [320px](create-320-after.png) |
| 服务详情左偏与裁切 | [320px](service-drawer-320-before.png) | [320px](service-drawer-320-after.png) |
| 保存失败消息被遮挡 | [320px](save-error-320-before.png) | [320px](save-error-320-after.png) |

[手机项目操作菜单](project-menu-320-after.png)验证换行后仍完整显示并可点击；该专项使用待成片场景，检查每个菜单项的完整边界和实际点击命中。[真实键盘焦点环](drawer-focus-390.png)验证服务抽屉的右侧绘制没有被根滚动条预留轨道裁掉。

审计覆盖17视图×6宽度×浅深主题共204组合、7类弹窗/菜单84组合，以及长标题、长错误、菜单命中、嵌套弹窗和导航转弹窗。错误、抽屉及嵌套锁开关的实际内容锚点最大位移0px；不是仅检查控件外框或内部root宽度。[范围与实测摘要](audit-summary.json) · [问题及验证说明](../../UI_FINAL_AUDIT.md)。

![修复后的桌面待成片页头](header-1440-pending-after.png)

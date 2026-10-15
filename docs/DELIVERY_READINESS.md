# 可核验交付与平台提交边界

本功能复用已有 `factory.acceptance` 技术/媒体/人工证据验收，不重写视频导出器，也不会生成、重编码或上传视频。现有交付物包括带字幕 MP4、无字幕 MP4、SRT、封面、剪辑时间线、声音编辑和技术检查记录。

## 在线只读检查

`GET /api/factory/projects/{id}/delivery-readiness` 返回当前项目版本的材料清单；原 `/flow` 也附带 `delivery_readiness`，避免团队页额外轮询。

该在线视图只读取项目快照，不读取媒体文件，不扫描验收历史、不运行 FFmpeg 或模型。每次检查材料路径、项目/制作代次、同一 release 目录、唯一文件名、有效 SHA/字节回执及当前已应用的人工终审。旧版本、验证代理批准、总制片代理批准或单独修改 accepted_by 均不能冒充出品人终审。

状态 `ready_for_fresh_acceptance` 只意味着已有记录齐全，`engineering_ready` 仍为 false。历史质检通过和材料回执不代表文件当前仍完整。尺寸、时长等以项目生产规格及验收结果为准，不声称某一画幅就是红果统一官方规格。

## 独立提交材料清单

```bash
python -m factory.submission \
  --project-id 012345abcdef \
  --project-json project-snapshot.json \
  --root . \
  --output runtime/submissions \
  --production-revision 2
```

也可以用 `--database` 指向实际现有 SQLite 文件，二者必须且只能提供一个。数据库按现有验收路径只读打开；不会初始化、迁移、修改批准状态或调用 worker。

命令先重新执行当前验收协议，检查真实媒体和哈希，再将报告绑定到观察到的项目快照及制作版本；结束时再次核对输入快照。验收结果输出原有不可覆盖报告目录；提交清单输出**另一个**全新目录，引用报告路径和 SHA，不往原 export-manifest 已封存的报告里添加文件。

只有当前材料记录、真实验收及人工终审都满足条件，才返回 `engineering_ready_platform_pending`。退出 0 只表示工程交付就绪，不表示平台已审核。缺片、过期证据或质量问题返回 2 并保留问题清单。所有状态的 `platform_ready` 都为 false，`platform_approval` 都为 not_verified。

## 出品方待核验

红果官方后台的账号权限和当前提交规格、内容及备案/许可要求、剧本/音乐/字体/声音/人物授权、AI 显式/隐式标识和上传声明。在线检查不能替用户核验账号、签署授权、提供法律判断或保证审核通过。本轮没有平台连接、自动提交和真实生成请求。

官方 AI 标识说明：https://www.cac.gov.cn/2025-03/14/c_1743654685896173.htm 。该链接是用户核对要求的参考，不是系统已满足全部要求的证明。

## 验证与回滚

`python -m pytest -q tests/test_factory_delivery_readiness.py tests/test_factory_acceptance.py tests/test_factory_evaluation.py`。覆盖旧批准、非人工批准、跨项目/跨版本材料、重复材料、路径穿越、伪造回执、报告绑定和真实空片验收。成功绑定的单元测试使用模拟报告，仅证明控制逻辑；空片用例执行真实验收，不能被描述为真实 AI 样片通过。

源码回退不会修改现有媒体、项目或人工批准记录；新增清单是独立普通文件，后续可按项目留存策略处理，无数据库反向迁移。本轮仅发布 Draft PR，不部署到生产。

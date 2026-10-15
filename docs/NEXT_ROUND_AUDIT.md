# 下一轮审查：优化 10 完整发布

## 基线与交付边界

实际重新 fetch 的 main：`4aaece74ab54f38c125c564accc20f4134fb5bab`。
正式 [PR #11](https://github.com/Xiiiing/ai-studio/pull/11)，head 分支
`feat/versioned-creative-prompts`，base `main`。本 PR 包含完整原实现及发布前修复，
不混入优化 7–9、11–15 的业务代码，不自动合并。

原实现本地 commit：`18f3af14a3a3dc3ea99044dd48238d6fc7526752`；原完整 tree：
`ed71687fd3660061fc9396ccf9d286b2ff90b332`。原实现远端 commit：
`89c7495b06e36a3df37dba73119ef02f84c3e51c`；修复版代码 commit：
`4244f7285e1170ca333bd03ac80ca7bf23a47d42`，tree：
`14a4aedacef61f73850bfef5c52418b8128a7152`。

## 问题、证据和改造

| 实际发现 | 处理方式与验证 |
| --- | --- |
| 五个主要创作 Prompt 嵌入业务代码，回执缺少独立版本归属 | 外置 catalog/v1 文本，职责、输入上下文和输出 Schema 引用独立于模型；字节等价及原缓存复用测试通过 |
| 缺少冻结输入与完整重复样本的匿名创作比较 | 独立离线 freeze/import/blind/compare，复用既有六个文本审稿维度；缺项不出汇总成绩，保留退化结果 |
| 自定义 catalog 可携带 Schema 引用 | 校验实例前拒绝 $ref/$dynamicRef/$recursiveRef；3 项受控回归修复前失败、修复后通过；未声称已发生外部请求 |
| recorded 结果可配上矛盾的 failed 外部收据 | 严格对应成功/失败状态；合成反例现被拒绝 |
| 证据写入可超过读取上限 | 写前执行同一 2 MiB 上限；拒绝时不创建文件 |
| holdout 直到 compare 才消费，输入已能再次导出 | creative_holdout.py 在导出前按稳定 case ID、plan 与 seal 预留；改种子或换 seal 不能重复发布，中断保留记录 |
| 上次发布漏传语料且 create_commit 参数错误 | 补齐完整 corpus，核对原 tree，使用 parent_sha 创建提交；创建真实正式分支和 PR 后回读差异 |

所有反例使用临时目录和合成收据，不是生产 HTTP 漏洞利用报告。既有 holdout 测试
加强为在第二次导出前拒绝，没有删除、跳过或放宽测试。

## 上游源码与许可

Toonflow：`HBAI-Ltd/Toonflow-app@d2e94aca7b59659ff6b4ca15f03cf134ec7f0e1d`，MIT。
实际研究 `packages/teams/storyboardTeam/team.json`、该目录下的
`members/director.md`、`members/writer.md`、`members/reviewer.md`、`knowledge/continuity.md`。
学习职责、指令、知识和权限分离；适配为原 Python/LangGraph 架构，未引入另一团队运行时或前端框架。

Drama Skills：`zenstory-ai/drama-skills@f53fb206507cb0cfb07fd29b692156dd4fb8fe7d`，MIT。
实际研究 `evaluations/content_quality_gate.py`、`tests/test_content_quality_gate.py`、
`evaluations/README.md`、`evaluations/content-quality-rubric.md`、
`skills/short-drama-write/SKILL.md`、`skills/short-drama-assets/references/identity-vs-variant.md`、
`skills/short-drama-storyboard/references/review-and-fixtures.md`。
学习冻结证据、完整采样与开发/保留集边界；未采用上游运行器、评分权重或语料。
两者许可证均实际读取；没有复制第三方代码、Prompt、测试或故事。
详细取舍见 `VERSIONED_CREATIVE_PROMPTS.md`。

## 安全、兼容与残余边界

保留 LangGraph + SQLite、两轮独立审稿、人工反馈优先级、原缓存签名及选片 CAS。
旧缓存不重写，历史 Prompt 版本未知就保持 null；新回执只增加字段。无数据库迁移，
无生产数据库/配置读写，无服务重启，无模型/GPU/收费调用，无批准素材覆盖。
可信 seal/ledger 需由操作者保管，不是认证系统；删除 ledger 不能让已见内容变成未见。
六个原合成案例完整保留，覆盖三种制作规格；公开 holdout 只测试协议，不是未见质量证据。
本轮没有证实艺术质量提升，也不报告提升比例。优化 11–15 在本 PR 中 not_run。

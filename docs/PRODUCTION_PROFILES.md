# 创作规格 production_profile（优化 5）

## 实施前数据模型和兼容约定

不新增状态数据库，也不批量迁移旧项目。旧项目缺少 production_profile 时读取 legacy15：15 镜、60–90 秒、female/male 两位角色，原提示词/回执兼容。新工作台显式选择规格，将标准化规格固定到项目、剧本、回执和导出中。

规格字段：schema_version=1、preset、shot_count、duration_seconds、shot_seconds、characters。characters 使用 character_id、name、voice_id（已登记声音别名）、可选 reference_asset（已批准系列 asset_id+version）；不以性别推断角色身份或声线。逐镜 dialogue.role 等既有执行字段承载稳定 character_id，不改成第二套执行格式。

新增 compact6：6 镜30秒，默认两个稳定角色；ensemble10：10镜50秒，默认三个稳定角色。非 legacy 规格有界支持3–24镜、15–90秒、1–4位角色、每镜3–10秒。时长必须对齐24fps，总和精确一致。无声镜头允许；配音预估是规划约束，实际声音占用仍由实测音频检查和有界压缩决定，不能拿文字估计冒充实测。

镜头、角色Schema，编剧/导演提示词，技术校验和生成回执共用同一规格解析器。旧配置不写入新字段；显式新规格在创建时验证。参考资产只接受已批准且通过文件校验的同系列固定版本，新剧集生成实际镜头后记录依赖；不自动升级最新版。

## 创建与运行

工作台创建表单从 `GET /api/factory/production-profiles` 取得预设。默认选择 legacy15；选择新规格后不继承经典表单的60秒设置。可选输入逐镜秒数和人物JSON；最终校验在后端完成，不能用前端编辑绕过上限。

```json
{"brief":"成年人在车站归还一本书的闭合故事","format":"motion_comic","production_profile":{"preset":"compact6","shot_seconds":[3,4,5,6,5,7],"characters":[{"character_id":"char_mei","name":"小梅","voice_id":"female"},{"character_id":"char_zhou","name":"小周","voice_id":"male"}]}}
```

此例第六镜7秒，若切换到6秒云视频模型会被模型时长校验拒绝；应选供应商支持的更长规格或改短该镜。局部重剪允许缩短镜头，但不改变原创作规格；导出同时记录原规格和实际时间线。声音别名在 `audio/voices.json` 或既有 episode.voices 中登记，未知 voice_id 不会退回默认男声。一个声线可由不同明确角色使用，不代表二者是同一身份。

角色 `reference_asset` 为 `{"asset_id":"asset_...","version":1}`。创建时验证同系列、同 character_id、正式 approved 状态和媒体校验；剧本生成后仅为出现该角色的镜头登记引用及依赖。后续工作台明确选定的引用优先于规格默认值。

## 可复现 CPU 样例与回归

```bash
python -m pytest -q tests/test_factory_profiles.py
python -m pytest -q tests/test_factory_*.py
npm ci --ignore-scripts
node --test tests/test_factory*.mjs
python scripts/profile_mock_demo.py --preset compact6 --output runtime/profile-examples/compact6
python scripts/profile_mock_demo.py --preset ensemble10 --output runtime/profile-examples/ensemble10
```

输出目录必须位于当前 checkout 的 runtime 中且尚不存在。样例运行模拟提案/编剧/导演、合成图像和正弦波语音，实际执行CPU媒体规范化、字幕、配音时长检查与MP4导出，最后再次恢复验证缓存复用。目录内包含project.json、summary.json和完整模拟素材回执。`artistic_quality_passed=false` 永不因这些模拟测试通过而变成true。

测试包含三种规格的完整CPU媒体路线、各层相同schema/角色、重复解析幂等、旧75秒与61秒分配、非法范围/NaN/布尔值/非帧边界、缺失或重复角色、未知声线、参考资产身份边界、媒体签名、实际导出字段及HTTP/前端创建。新字段不要求旧历史回执具备；新增规格不承诺任意长片、多人口型、复杂群体动作或真实模型效果。

## 实际影响范围

后端：production_profiles.py、profile_creative.py、profile_references.py；既有store/schemas/creative/storyboard/speech/production/audio_editor/acceptance/web以及studio/planning导出。前端：独立production-profile.js加创建表单/API连接。所有变更在隔离分支和临时数据库验证，未迁移运行实例。


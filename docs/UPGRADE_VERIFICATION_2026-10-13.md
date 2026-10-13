# 六项升级：隔离实现与本地验证记录

开发根目录为WebCodex管理的独立worktree；没有修改正在运行的生产数据、没有部署或重启服务，也没有调用真实收费API。所有新模型调用测试均为mock或loopback测试服务，真实媒体操作仅CPU FFmpeg/PyAV；浏览器在network=none的官方Playwright容器中运行。

## 已核实的本地检查点

| 优化 | 独立分支 | Python | 前端 | 补充验证 |
| --- | --- | --- | --- | --- |
| 1 云费用/授权 | feat/cloud-video-budget | 388通过 | 37通过 | 原有PR #2，不重复实现；远端代码树f55c547f777f95a8f82aa2ac7f02c983a778c8eb已核验 |
| 2 远程调度 | feat/remote-video-scheduling | 399通过 | 37通过 | 11项调度测试含进程退出、长等待、交替推进、未知POST保护 |
| 3 系列资产 | feat/series-bible-assets | 419通过 | 43通过 | 跨集复用、批准校验、精确依赖、符号链接、未完成云任务阻断 |
| 4 逐镜工作台 | feat/shot-production-workbench | 435通过 | 50通过 | 16项后端交互契约、人工选片/不可覆盖/CAS/局部失效 |
| 5 可配置规格 | feat/production-profiles | 462通过 | 54通过 | 6镜30秒、10镜50秒、15镜60秒三条模拟媒体路线及缓存恢复 |
| 6 成片评估 | feat/quality-evaluation | 494通过 | 54通过 | 32项评估控制；真实Chromium 5项冒烟全部通过 |

最终Python：494 passed in 78.21s。最终浏览器：5 passed in 8.0s。前端：54 passed、0 failed。既有测试未删除或放宽以隐藏失败；旧固定质检缓存的兼容测试发现一次签名变化后修复，重新跑全套通过。浏览器初次两处重复选择器导致测试定位失败，限定导航范围后重新跑全套通过。另修复请求失败时保留播放节点但禁用编辑的真实前端缺陷。

以上数字是各检查点累积测试数量，不能相加为独立测试总数。它们不是GitHub Actions远端执行结果，也不是实际生成画面的质量成绩。以前部分fork测试产生过Python警告；本轮最终完整输出没有警告项。

## 复现命令

```bash
python -m pip install -r requirements-factory.txt -r requirements-evaluation.txt
python -m pytest -q tests/test_factory_*.py
npm ci --ignore-scripts
node --test tests/test_factory*.mjs
python scripts/browser_fixtures.py
npx playwright install --with-deps chromium
npm run test:browser
```

本次没有安装依赖到运行中生产环境。控制Python借用已有解释器，新增OpenCC/pypinyin仅装到worktree的runtime/evaluation-deps；前端依赖在独立node_modules中。旧glibc宿主使用独立Node22二进制，浏览器使用与@playwright/test 1.64.0匹配的官方容器。

## 样例和评估证据

模拟样例保存在runtime/profile-examples/compact6和ensemble10，包含project.json、summary.json和完整回执/媒体；明确fixture=synthetic_images_and_sine_wave_audio_not_model_inference。配音为合成测试声音，人物为程序图案，不是实际模型推理。

只读评估报告保存在runtime/evaluation-examples/compact6-release和ensemble10-release。两者均technical_passed=true、verdict=human_review_required、human_approved=false、artistic_quality_certified=false。ASR/VLM/VBench真实推理均未运行，不补造分数。

实际浏览器证据：runtime/browser-report/results.json、models.png、shot-workbench.png；检查了project列表、模型编辑/预检、视频预览、终审决策、播放期间进度更新、失败提示和旧版本草稿。video保持同一个连接中的节点，currentTime继续增长，无emptied事件。截图展示合成测试片，不表示人物画质。

本地原始日志：runtime/verification/series-python.log、series-ui.log、workbench-final-python.log、workbench-final-ui.log、profile-final-python.log、profile-final-ui.log、evaluation-release-python.log、evaluation-release-ui.log、evaluation-release-browser.log。runtime输出不进入源代码提交。

## 发布状态和数据库边界

优化1已有PR https://github.com/Xiiiing/ai-studio/pull/2。其余代码在独立本地分支；GitHub连接对新增对象写入返回平台安全状态拦截，普通git push预检也没有可用的HTTPS身份。没有绕过拦截、索取或记录密钥；未发布分支不能标作已创建PR。本文件不声称远端五个新增PR已创建或CI已运行。

为防止重复包含已发布优化1，后续发布栈可用每个已测试tree依次创建以PR #2提交为父的提交，保留原本地开发分支不动。发布时每项PR的base为前一项分支；不自动合并、不强推、不直接修改main。

数据库迁移详见各专题文档：优化1新增四张费用表；优化2为jobs/cloud_tasks增量添加phase、next_poll_at、lease及失败计数；优化3新增三张系列表；优化4/5按需追加项目快照字段，旧数据不批量改写；优化6只读数据库并输出报告，无新增状态存储。上线迁移需要操作者另择维护窗口、一致性备份和明确部署操作，本次未对运行实例执行迁移。

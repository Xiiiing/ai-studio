# 独立短剧与画面媒介契约

新建页明确区分 2D 动漫、3D 动画、真人写实和动态漫画。前三种必须生成实际视频；动态漫画继续使用静态插画加程序运镜，不能显示为已制作的连续动漫。

新客户端创建项目时提交 `creative_contract`。它是生产意图，不是模型能力或成片质量证明：

```json
{
  "format": "animation",
  "creative_contract": {
    "schema_version": 1,
    "visual_style": "anime_2d",
    "story_mode": "standalone",
    "pacing": "short_drama",
    "motion": "generated_video"
  }
}
```

| 制作形态 `format` | 画风 `visual_style` | 运动 `motion` |
| --- | --- | --- |
| `animation` | `anime_2d` / `stylized_3d` | `generated_video` |
| `live_action` | `photorealistic` | `generated_video` |
| `motion_comic` | `illustrated` | `still_camera` |

当前版本仅支持 `schema_version: 1`、`story_mode: standalone` 和 `pacing: short_drama`。显式契约要求字段完整；未知字段、画风与制作形态或运动方式不一致，在入队前拒绝。省略整个字段的历史 API、项目和检查点继续原行为，不自动补造契约，也不改变它们的创作缓存签名。

契约进入故事、目标落实、编剧、独立审稿、分镜、视觉方案、总制片的系统约束及缓存上下文，并保存在批准的 episode 中。系统约束要求开场可见冲突、主角明确目标、因果转折和兑现结尾，在所选片长内完成一个事件，不以「下集待续」代替结局。视觉候选在同一媒介内改变色板、光照和造型，不能用真人或 3D 候选替代用户指定的 2D 动漫。

生成视频每镜要求一个简单、连续、可观察的人物或物体动作；只有静态画面上的推拉摇移不满足动画要求。关键帧仅代表动作的起始瞬间。仍使用画外音、可见人物闭嘴，不承诺口型同步、复杂多人动作或未经真实媒体检查的制作能力。最终成片仍由用户审片。

验证分为离线契约/缓存/兼容测试和实际模型媒体验收。提示词约束及离线测试不能代替对具体模型输出的画风、动作连续性和剧情闭环检查。

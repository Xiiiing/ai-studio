"""The same approved starting state drives image repair and image review."""
from __future__ import annotations

import copy


def starting_frame(shot):
    result = copy.deepcopy(shot)
    for field in ('action', 'motion_prompt', 'continuity', 'dramatic_beat'):
        result.pop(field, None)
    states = result.get('character_states')
    if isinstance(states, dict):
        for state in states.values():
            if isinstance(state, dict):
                state.pop('story_state', None)
    return result


APPROVED_ACTION_RULE = (
    '以本镜原批准的shot.action和motion_prompt为动作验收依据，不得套用后来提示词模板的更大动作幅度。'
    'dramatic_beat、情绪和story_state不能自动新增动作要求。结印、支撑或保持姿势允许持续状态与细微变化，'
    '不得额外要求迈步、明显位移、剧烈摆动或大幅挣扎；但原文明确要求起身、递物、转头等动作仍必须兑现。'
    '不同人物分别核验，批准静止的人物无需运动。细微手部、面部或姿态变化须引用跨时间可见证据；'
    '相机和能量特效变化不能冒充人物运动。抽样无法确定细微动作时填uncertain并补证，不能臆造完全静止或强制通过。'
)

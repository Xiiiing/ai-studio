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


RENDER_STYLE_RULE = (
    'approved_render_style及approved_visual_style记录项目已选或逐镜明确的渲染媒介和画风，人物参考只提供身份与衣着，参考自身的媒介不能覆盖它。'
    'fantasy anime、玄幻或动漫等泛化题材/美术词本身不等于二维；它们可以按已选三维电影动画/PBR媒介呈现。'
    '不能凭泛化词或原作记忆将明确已选的三维媒介改成二维赛璐璐，也不能把所有动漫一概视为二维。'
    '若两处确实分别明确要求二维和三维且无批准的覆盖关系，才报告真正的媒介冲突；不得自行改写明确的媒介要求。'
)

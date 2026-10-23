"""The same approved starting state drives image repair and image review."""
from __future__ import annotations

import copy


CAMERA_EXECUTION = {
    'pan_right': 'The camera pans horizontally to the right; stationary background landmarks travel left across the frame as the view turns right.',
    'pan_left': 'The camera pans horizontally to the left; stationary background landmarks travel right across the frame as the view turns left.',
    'push_in': 'The camera moves closer to the subject; the subject and background framing grow larger as the viewing distance decreases.',
    'pull_out': 'The camera moves away from the subject; the framing widens as the viewing distance increases.',
    'hold': 'The camera holds its position, orientation and framing while the approved subject action unfolds.',
}


def camera_execution(motion, fallback=''):
    """Translate the approved enum, never a dramatic beat, into visible motion."""
    return (motion + ': ' + CAMERA_EXECUTION[motion]) if motion in CAMERA_EXECUTION else fallback or motion


CAMERA_REVIEW_RULE = (
    'camera_observation只记录候选帧中实际观察到的主要相机运动，不照抄批准值。'
    '用至少两个实际样本中的背景固定参照物位置、尺度变化判断；人物走动、转身和特效不能代替相机运动。'
    'pan_right表示相机向右转动、固定背景向画面左侧移动；pan_left反之；push_in/pull_out是景别尺度的前推/后拉变化。'
    'dramatic_beat中的推进指剧情推进，不是push_in，也不能覆盖camera_motion。'
    '多种明显运动混合填mixed；无法从样本证实填uncertain，不猜测批准动作已经发生。'
    'from_sample和to_sample用本次候选实际序号，evidence具体说明两个时间点的固定背景或景别变化。'
)


def camera_observation_schema():
    return {'type': 'object', 'additionalProperties': False,
            'required': ['motion', 'from_sample', 'to_sample', 'evidence'],
            'properties': {'motion': {'enum': [*CAMERA_EXECUTION, 'mixed', 'uncertain']},
                           'from_sample': {'type': 'integer', 'minimum': 1},
                           'to_sample': {'type': 'integer', 'minimum': 2},
                           'evidence': {'type': 'string', 'minLength': 8, 'maxLength': 800}}}


def verify_camera(result, shot, sampling, media_sha):
    """A general passing score cannot waive an explicitly different movement."""
    result = copy.deepcopy(result)
    row = result.get('camera_observation') or {}
    times = sampling['timestamps']
    first, last = row.get('from_sample'), row.get('to_sample')
    valid = (type(first) is int and type(last) is int and 1 <= first < last <= len(times))
    observed, expected = row.get('motion'), shot['camera_motion']
    status = ('unavailable' if not valid or observed in {'mixed', 'uncertain', None}
              else 'pass' if observed == expected else 'fail')
    result['camera_verification'] = {
        'policy': 'ai-studio.camera-contract.v1', 'status': status,
        'expected_motion': expected, 'observation': row, 'media_sha256': media_sha,
        'timestamps': list(times), 'scope': 'sampled_camera_motion_not_exhaustive_video'}
    if status == 'fail':
        result.setdefault('issues', []).append({'code': 'action', 'severity': 'blocking',
            'detail': f'批准运镜为{expected}，实际样本{first}至{last}观察到{observed}：{row["evidence"]}'})
    elif status == 'unavailable':
        result['_constraint_unavailable'] = True
    return result


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

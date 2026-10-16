"""Bounded text-only repair policy shared by both creative graph versions."""


def max_review_rounds(config):
    value = config.get("creative", {}).get("max_review_rounds", 4)
    if type(value) is not int or not 1 <= value <= 6:
        raise ValueError("creative.max_review_rounds must be an integer from 1 to 6")
    return value


def automation(config, phase, round_index, generation, status, reason):
    return {
        "phase": phase,
        "status": status,
        "round": round_index,
        "max_rounds": max_review_rounds(config),
        "automatic_repairs": max(0, round_index - 1),
        "generation": generation,
        "reason": reason,
    }


def exhausted_message(label, round_index):
    return "%s已完成 %d 轮独立审核、自动返修 %d 次，仍有阻断项；请人工选择修改方向，或让 AI 辅助分析后继续。" % (
        label, round_index, max(0, round_index - 1)
    )

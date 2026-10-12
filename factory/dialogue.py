"""Bounded dialogue compression driven by measured synthesized speech duration."""
from __future__ import annotations

from studio.core import ROOT
from .config import load_config
from .llm import json_call

SCHEMA={"type":"object","additionalProperties":False,"properties":{
    "reason":{"type":"string"},
    "line_updates":{"type":"array","minItems":1,"maxItems":2,"items":{
        "type":"object","additionalProperties":False,
        "properties":{"id":{"type":"string"},"text":{"type":"string","minLength":1,"maxLength":30},
                      "emotion":{"type":"string"}},
        "required":["id","text"]}}},
    "required":["reason","line_updates"]}


def _spoken(text):
    return sum(c.isalnum() for c in text)


def compress_dialogue(project, shot, measurement):
    lines={line["id"]:line for line in shot.get("dialogue",[])}
    if not lines:
        raise ValueError("无可压缩的对白")
    result=json_call(load_config(),"dialogue_editor",
        "你是对白时长编辑。实际TTS已经证明部分台词超出镜头时长。只压缩这些台词的措辞，"
        "保留事实、关键道具、因果、否定词、人物立场与语义；不能改剧情、说话角色、镜长或画面。"
        "必须使用原有台词id；每条新台词的可发声字符数须严格少于原句，通常缩到原句的50%至70%。"
        "禁止用省略号、长停顿、吞字或标签假装缩短。measurement给出的超时行优先处理。"
        "emotion可省略以沿用原来的语气。reason用中文说明怎样保留原意。严格返回JSON。",
        {"project_title":project.get("title"),"story":(project.get("script") or {}).get("synopsis",""),
         "shot_id":shot.get("id"),"dramatic_beat":shot.get("dramatic_beat"),
         "action":shot.get("action"),"seconds":shot.get("seconds"),
         "dialogue":list(lines.values()),"measurement":measurement},
        SCHEMA,ROOT/"production"/project["slug"]/"quality"/"dialogue_rewrites",
        max_tokens=1400,temperature=.2)
    seen=set()
    for update in result["line_updates"]:
        ident=update["id"]
        if ident not in lines or ident in seen:
            raise ValueError("缩句结果必须唯一对应现有台词")
        seen.add(ident)
        update["text"]=update["text"].strip()
        if not 0 < _spoken(update["text"]) < _spoken(lines[ident]["text"]):
            raise ValueError("缩句结果没有减少可发声字符")
        update.setdefault("emotion",lines[ident].get("emotion","自然"))
    return result

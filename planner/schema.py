"""Strict episode JSON contract and deterministic timing checks."""
from __future__ import annotations
import math
import re
import copy

TYPOGRAPHIC_ASCII = str.maketrans({"\u2014": "-", "\u2013": "-", "\u2011": "-", "\u2010": "-",
                                  "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                                  "\u2026": "...", "\u00a0": " ", "\u202f": " "})

def normalize_episode(value):
    """Normalize typography in English fields without translating or changing story content."""
    result = copy.deepcopy(value)
    changes = []
    if not isinstance(result, dict):
        return result, changes
    fields = [(result, key, key) for key in ("visual_style", "english_title") if key in result]
    if isinstance(result.get("cast"), dict):
        fields += [(result["cast"], role, "cast."+role) for role in ("female", "male") if role in result["cast"]]
    if isinstance(result.get("shots"), list):
        fields += [(shot, "prompt", f"shots[{i}].prompt") for i, shot in enumerate(result["shots"]) if isinstance(shot, dict) and "prompt" in shot]
    for parent, key, label in fields:
        old = parent[key]
        if not isinstance(old, str):
            continue
        new = re.sub(r"\s+", " ", old.translate(TYPOGRAPHIC_ASCII)).strip()
        if new != old:
            parent[key] = new
            changes.append({"field": label, "operation": "typography_and_whitespace_to_ascii",
                            "non_ascii_characters": sorted(set(x for x in old if ord(x) > 126))})
    return result, changes

def chinese_prose(value):
    if not re.search(r"[\u3400-\u9fff]", value):
        return False
    words = re.findall(r"[A-Za-z]{2,}", re.sub(r"S[0-9]{2,3}", "", value))
    return all(word in {"AI", "VR", "AR"} for word in words)

def text_schema(minimum=1, maximum=1200):
    return {"type": "string", "minLength": minimum, "maxLength": maximum}

def english_schema(minimum, maximum, description):
    # Keep server grammar structural; language checks are applied after decoding.
    return {**text_schema(minimum, maximum),
            "description": "English prose using printable ASCII. " + description}

def obj(properties, optional=()):
    return {"type": "object", "properties": properties, "required": [key for key in properties if key not in optional], "additionalProperties": False}

def generation_settings(settings):
    fps, seconds = settings["fps"], settings["seconds_per_shot"]
    trim = int(round(fps * seconds))
    frames = math.ceil((trim - 1) / 4) * 4 + 1
    return {
        "primary_model": "Wan2.2-TI2V-5B", "precision": "fp16",
        "width": settings["generation_width"], "height": settings["generation_height"],
        "crop_to": [settings["width"], settings["height"]], "fps": fps,
        "frames_per_shot": frames, "generated_seconds_per_shot": frames / fps,
        "trim_frames_per_shot": trim, "trim_seconds_per_shot": seconds,
        "fallback_model": "MiniMax-H3", "aspect_ratio": "9:16", "subtitles": "postproduction",
    }

def episode_schema(slug, settings):
    count, seconds = settings["shots"], settings["seconds_per_shot"]
    g = generation_settings(settings)
    dialogue = obj({
        "id": {"type": "string", "pattern": "^S[0-9]{2,3}_L1$"},
        "role": {"type": "string", "enum": ["female", "male"]},
        "text": {**text_schema(1, 80), "description": "Spoken simplified Chinese, at most 18 Hanzi."},
        "emotion": {**text_schema(1, 160), "description": "Acting direction in simplified Chinese, never English."},
        "start": {"type": "number", "enum": [round(i / 10, 1) for i in range(1, 11)]},
        "audio_effect": {"type": "string", "enum": ["telephone"]},
    }, optional=("audio_effect",))
    shot = obj({
        "id": {"type": "string", "pattern": "^S[0-9]{2,3}$"},
        "seconds": {"type": "number", "const": seconds},
        "prompt": english_schema(350, 750, "A single filmable visual action, lens, composition, lighting and fixed character details. No generated readable words, letters, numerals, subtitles or logos. Tell the story through shapes, gesture and physical action."),
        "dialogue": {"type": "array", "minItems": 1, "maxItems": 1, "items": dialogue},
        "reference_group": {"type": "string", "enum": ["female", "male", "two"]},
        "continuity": text_schema(15, 120), "sfx": text_schema(1, 70),
        "frames": {"type": "integer", "const": g["frames_per_shot"]},
        "trim_frames": {"type": "integer", "const": g["trim_frames_per_shot"]},
        "timeline_start": {"type": "number", "enum": [i * seconds for i in range(count)]},
        "timeline_end": {"type": "number", "enum": [(i + 1) * seconds for i in range(count)]},
        "lip_sync": {"type": "boolean"},
    }, optional=("lip_sync",))
    return obj({
        "title": text_schema(1, 100), "slug": {"type": "string", "const": slug},
        "logline": text_schema(20, 400),
        "cast": obj({role: english_schema(160, 1000, "One fictional adult character, Romanized name, numeric age at least 18, fixed face, hair and wardrobe.") for role in ("female", "male")}),
        "visual_style": english_schema(80, 500, "Photographic style, palette, light and composition; avoid any generated typography."),
        "english_title": english_schema(3, 100, "An original English title."), "poster_tagline": text_schema(3, 80),
        "width": {"type": "integer", "const": settings["width"]},
        "height": {"type": "integer", "const": settings["height"]},
        "fps": {"type": "integer", "const": settings["fps"]},
        "total_seconds": {"type": "number", "const": count * seconds},
        "generation": {"type": "object", "const": g},
        "shots": {"type": "array", "minItems": count, "maxItems": count, "items": shot},
    }, optional=("english_title", "poster_tagline"))

def validate_schema(value, schema, location="$"):
    errors = []
    kind = schema.get("type")
    good = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
    }
    if kind and not good[kind]():
        return [f"{location}: expected {kind}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: must be one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        if properties:
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"{location}.{key}: missing")
            if schema.get("additionalProperties") is False:
                for key in value.keys() - properties.keys():
                    errors.append(f"{location}.{key}: unexpected field")
            for key in value.keys() & properties.keys():
                errors.extend(validate_schema(value[key], properties[key], f"{location}.{key}"))
    elif kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            errors.append(f"{location}: unexpected item count {len(value)}")
        for i, item in enumerate(value):
            errors.extend(validate_schema(item, schema["items"], f"{location}[{i}]"))
    elif kind == "string":
        if len(value.strip()) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            errors.append(f"{location}: text length outside bounds")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{location}: pattern mismatch")
    elif kind in ("number", "integer"):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            errors.append(f"{location}: number outside bounds")
    return errors

def validate_episode(value, slug, settings):
    errors = validate_schema(value, episode_schema(slug, settings))
    if errors:
        return errors
    seconds = settings["seconds_per_shot"]
    line_ids = set()
    english_fields = [(f"cast.{role}", value["cast"][role]) for role in ("female", "male")]
    english_fields.append(("visual_style", value["visual_style"]))
    english_fields += [(f"{s['id']}.prompt", s["prompt"]) for s in value["shots"]]
    if "english_title" in value:
        english_fields.append(("english_title", value["english_title"]))
    for field, content in english_fields:
        if not all(32 <= ord(char) <= 126 for char in content) or len(re.findall(r"[A-Za-z]", content)) < 3:
            errors.append(f"{field}: English printable ASCII required; use Romanized names")
    for field in ("title", "logline"):
        content = value[field].removeprefix("测试草稿：") if field == "title" else value[field]
        if not chinese_prose(content):
            errors.append(f"{field}: use simplified Chinese prose and Chinese role descriptions, not English sentences")
    for index, shot in enumerate(value["shots"]):
        expected_id = f"S{index+1:02d}"
        for field in ("continuity", "sfx"):
            if not chinese_prose(shot[field]):
                errors.append(f"{expected_id}.{field}: use Chinese prose; replace English names with Chinese role descriptions")
        if shot.get("lip_sync") and not shot["dialogue"]:
            errors.append(f"{expected_id}: lip_sync needs a speaking line")
        if shot.get("lip_sync") and shot["dialogue"] and shot["reference_group"] != shot["dialogue"][0]["role"]:
            errors.append(f"{expected_id}: lip_sync needs one visible speaking character; reference_group must match the speaker role")
        if shot["id"] != expected_id:
            errors.append(f"shots[{index}].id must be {expected_id}")
        if not math.isclose(shot["timeline_start"], index * seconds, abs_tol=1e-6):
            errors.append(f"{expected_id}: incorrect timeline_start")
        if not math.isclose(shot["timeline_end"], (index + 1) * seconds, abs_tol=1e-6):
            errors.append(f"{expected_id}: incorrect timeline_end")
        for line in shot["dialogue"]:
            if line["id"] != expected_id + "_L1" or line["id"] in line_ids:
                errors.append(f"{expected_id}: dialogue id must be unique {expected_id}_L1")
            line_ids.add(line["id"])
            if not chinese_prose(line["emotion"]):
                errors.append(f"{line['id']}.emotion: use a Chinese acting direction")
            hanzi = len(re.findall(r"[\u3400-\u9fff]", line["text"]))
            if hanzi == 0 or hanzi > settings["max_hanzi_per_line"]:
                errors.append(f"{line['id']}: {hanzi} Hanzi; maximum {settings['max_hanzi_per_line']} for this shot")
            if line["start"] >= seconds:
                errors.append(f"{line['id']}: dialogue begins outside the shot")
    if sum(bool(s.get("lip_sync")) for s in value["shots"]) > math.ceil(len(value["shots"]) / 3):
        errors.append("Use lip_sync for at most one third of shots; include reaction shots and voiceover.")
    if not line_ids:
        errors.append("The story must contain spoken Chinese dialogue.")
    return errors

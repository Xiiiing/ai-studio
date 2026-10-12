"""The visual critic receives current evidence, never previous verdicts."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from factory import llm


@pytest.mark.parametrize("previous,fresh,expected", [
    ({"status": "fail", "score": 45},
     {"status": "pass", "score": 92, "summary": "Current candidate passes", "issues": []},
     "pass"),
    ({"status": "pass", "score": 100},
     {"status": "pass", "score": 95, "summary": "Missing required prop",
      "issues": [{"code": "continuity", "severity": "blocking", "detail": "Required prop is absent"}]},
     "fail"),
])
def test_visual_request_excludes_prior_verdict_and_worker_metadata(tmp_path, previous, fresh, expected):
    sample = tmp_path / "candidate.png"
    reference = tmp_path / "reference.png"
    sample.write_bytes(b"mock image")
    reference.write_bytes(b"mock reference")
    shot = {
        "id": "S03", "seconds": 4, "action": "The adult shows the silver key in one open hand.",
        "prompt": "Approved current scene", "reference_group": "male",
        "dialogue": [{"role": "male", "text": "请看。", "audio_path": "OLD_AUDIO_SECRET"}],
        "qa": {"semantic": {**previous, "summary": "OLD_REVIEW_SENTINEL"}},
        "attempts": [{"qa": previous, "path": "OLD_ATTEMPT_SENTINEL"}],
        "image": {"path": "OLD_IMAGE_SENTINEL"}, "status": "retrying",
        "audio_timing": {"diagnostic": "OLD_TIMING_SENTINEL"},
    }
    captured = {}
    def request(*args, **kwargs):
        captured["content"] = kwargs["content"]
        return deepcopy(fresh)
    config = {"quality": {"semantic_enabled": True, "minimum_score": 60},
              "llm": {"model": "test-visual-model"}}
    with patch.object(llm, "_image_url", return_value="data:image/png;base64,bW9jaw=="), \
         patch.object(llm, "_request", side_effect=request):
        project = {"slug": "factory-test-context", "format": "motion_comic",
                   "script": {"shots": [shot, {"id": "S07", "action": "The woman first gives him a food cup.",
                                             "continuity": "Food is introduced here.", "qa": "OLD_SEQUENCE_SENTINEL"}]}}
        result = llm.review_media(project, shot, sample, [reference], config)
    serialized = json.dumps(captured["content"], ensure_ascii=False)
    context = json.loads(captured["content"][0]["text"])
    assert context["shot"]["action"] == shot["action"]
    assert context["shot"]["prompt"] == shot["prompt"]
    assert context["approved_sequence"][1] == {"id": "S07", "action": "The woman first gives him a food cup.",
                                               "continuity": "Food is introduced here."}
    assert context["shot"]["dialogue"] == [{"role": "male", "text": "请看。"}]
    assert not any(key in context["shot"] for key in ("qa", "attempts", "image", "status", "audio_timing"))
    assert "OLD_" not in serialized
    assert len([item for item in captured["content"] if item["type"] == "image_url"]) == 2
    assert result["status"] == expected
    assert result["score"] == fresh["score"]
    assert shot["qa"]["semantic"]["summary"] == "OLD_REVIEW_SENTINEL"

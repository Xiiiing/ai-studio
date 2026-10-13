"""Real CPU media and mock semantic/ASR controls; no network or downloaded models."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import wave

import numpy as np
import pytest
from PIL import Image

from audio.check_dialogue import compare_transcript
from factory import evaluation as ev, evaluation_external as external, evaluation_sampling as sampling
from factory import evaluation_semantic as semantic, llm, store
from factory.audio_editor import FFMPEG
from factory.evaluation_io import check_receipt, database_project, export_report, row
from factory.evaluation_media import parse_srt
from studio import core


def receipt(root, path):
    return {"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size,"sha256":core.sha256(path)}


@pytest.fixture(scope="module")
def media_base(tmp_path_factory):
    root = tmp_path_factory.mktemp("golden-media")
    folder = root / "production/factory-012345abcdef"
    folder.mkdir(parents=True)
    def video(name, seconds, audio=False):
        path = folder / name
        args = [str(FFMPEG),"-nostdin","-hide_banner","-loglevel","error","-n","-f","lavfi","-i",f"testsrc=size=128x192:rate=24:duration={seconds}"]
        if audio:
            args += ["-f","lavfi","-i",f"sine=frequency=440:sample_rate=48000:duration={seconds}","-ac","2","-c:a","aac"]
        args += ["-c:v","libx264","-threads","1","-pix_fmt","yuv420p","-t",str(seconds),str(path)]
        subprocess.run(args,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        return path
    first, second = video("S01.mp4",1),video("S02.mp4",1)
    master, clean = video("episode.mp4",2,True),video("episode_clean.mp4",2,True)
    image = folder / "reference.png"; Image.new("RGB",(128,192),(30,80,120)).save(image)
    wav = folder / "line.wav"
    with wave.open(str(wav),"wb") as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000)
        stream.writeframes((np.sin(np.arange(12000)*2*np.pi*440/24000)*4000).astype('<i2').tobytes())
    subtitles = folder / "subtitles.zh.srt"
    subtitles.write_text("1\n00:00:00,100 --> 00:00:00,800\n你好\n\n2\n00:00:01,100 --> 00:00:01,800\n再见\n",encoding="utf-8")
    timeline, mix = folder/"timeline.json",folder/"audio_edit.json"
    timeline.write_text('{"shots":[{"shot":"S01","seconds":1},{"shot":"S02","seconds":1}],"duration_seconds":2}')
    mix.write_text('{"fixture":true}')
    shots, lines = [], []
    for i,(path,text) in enumerate(((first,"你好"),(second,"再见")),1):
        sid = "S%02d" % i
        audio = {**receipt(root,wav),"id":sid+"_L1","text":text,"role":"lead","quality":{"wav_sha256":core.sha256(wav)}}
        shots.append({"id":sid,"seconds":1,"video":receipt(root,path),"image":receipt(root,image),
                      "characters":["lead"],"prompt":"A fictional adult", "motion_prompt":"The book is handed over", "dialogue":[{"id":sid+"_L1","role":"lead","text":text,"start":.1}],
                      "audio_timing":{"fits":True},"attempts":[{"stage":"video","take":"take_001","attempt":1,"model":"mock-model-not-inference","state":"passed","elapsed_seconds":.5}]})
        lines.append(audio)
    project = {"id":"012345abcdef","slug":"factory-012345abcdef","production_revision":2,"revision":8,"format":"animation",
               "episode":{"shots":copy.deepcopy(shots),"cast":{"lead":"A fictional adult"}},"shots":shots,
               "assets":{"characters":{"lead":receipt(root,image)},"audio":{"lines":lines}},
               "delivery":{"path":receipt(root,master)["path"],"resolution":[128,192],"duration_seconds":2,
                           "artifacts":[receipt(root,p) for p in (master,clean,subtitles,timeline,mix)]}}
    return root,project


@pytest.fixture
def film(media_base,tmp_path):
    import shutil
    root,project = media_base
    shutil.copytree(root/"production",tmp_path/"production")
    return tmp_path,copy.deepcopy(project)


def test_golden_protocol_covers_all_seven_positive_negative_controls():
    golden = ev.read_json(ev.GOLDEN)
    assert len(golden["dimensions"]) == 7
    assert {value["dimension"] for value in golden["cases"]} == set(golden["dimensions"])
    assert all(value["positive"] != value["negative"] and value["expected_issue"] == value["dimension"] and value["human_required"] for value in golden["cases"])


def test_default_evaluation_does_not_infer_artistic_pass_or_call_models(film,monkeypatch):
    root,project = film
    def forbidden(*args,**kwargs):
        raise AssertionError("CPU evaluation may not contact a model")
    monkeypatch.setattr(llm,"_request",forbidden)
    report = ev.evaluate(project,root)
    assert report["technical_passed"] is True
    assert report["verdict"] == "human_review_required" and report["human_approved"] is False
    assert report["artistic_quality_certified"] is False
    assert all(value["status"] == "not_run" for value in report["checks"] if value["source"] in {"vlm","human","vbench","asr"})
    assert report["observations"]["records"][0]["model"] == "mock-model-not-inference"
    assert report["observations"]["records"][0]["confirmed_cost_micros"] is None


def test_json_csv_reports_are_auditable_append_only_and_formula_safe(film,tmp_path):
    root,project = film
    report = ev.evaluate(project,root)
    report["project_id"] = '=HYPERLINK("https://invalid")'
    out = tmp_path/"report"
    export_report(report,out)
    saved = ev.read_json(out/"report.json")
    assert saved["media_signature"] == report["media_signature"]
    with (out/"metrics.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(report["checks"]) and rows[0]["project_id"].startswith("'=")
    manifest = ev.read_json(out/"export-manifest.json")
    assert all(core.sha256(out/item["path"]) == item["sha256"] for item in manifest["files"])
    assert ev.read_json(out/"human-review-template.json")["decision"] == "pending"
    with pytest.raises(FileExistsError):
        export_report(report,out)


@pytest.mark.parametrize("defect",["missing_export","tampered_video","bad_subtitle_text","subtitle_time","missing_shot"])
def test_real_negative_delivery_controls_fail_with_repair_guidance(film,defect):
    root,project = film
    if defect == "missing_export": project["delivery"]["artifacts"] = project["delivery"]["artifacts"][1:]
    elif defect == "tampered_video": (root/project["shots"][0]["video"]["path"]).write_bytes(b"corrupt")
    elif defect == "missing_shot": project["shots"].pop()
    else:
        value = next(value for value in project["delivery"]["artifacts"] if value["path"].endswith('.srt'))
        path = root/value["path"]
        text = path.read_text().replace("你好","错误") if defect == "bad_subtitle_text" else path.read_text().replace("00:00:01,800","00:00:03,800")
        path.write_text(text);value.update(receipt(root,path))
    result = ev.evaluate(project,root)
    assert result["verdict"] == "needs_repair" and result["technical_passed"] is False
    assert result["repair_suggestions"]


def test_timeline_order_cannot_disagree_with_actual_shots_even_with_valid_checksum(film):
    root,project = film
    value = next(value for value in project["delivery"]["artifacts"] if value["path"].endswith('timeline.json'))
    path = root/value["path"]
    timeline = json.loads(path.read_text())
    timeline["shots"].reverse()
    path.write_text(json.dumps(timeline));value.update(receipt(root,path))
    result = ev.evaluate(project,root)
    assert result["verdict"] == "needs_repair"
    assert any(item["check"] == "timeline_plan_matches_media" and item["status"] == "fail" for item in result["checks"])


def test_temporal_policy_changes_invalidate_review_not_generation_source(film,tmp_path,monkeypatch):
    from factory import production
    root,project = film
    monkeypatch.setattr(core,"ROOT",root);monkeypatch.setattr(production,"ROOT",root)
    a = production._Run(project,{"semantic_enabled":False,"temporal_sampling":{"policy":sampling.POLICY,"count":8}},None,None)
    b = production._Run(project,{"semantic_enabled":False,"temporal_sampling":{"policy":sampling.POLICY,"count":12}},None,None)
    def target(run):
        return run._target("S01",run.folder/"renders/S01",run.shots[0]["motion_prompt"],kind="video",seconds=1,shot=run.shots[0],review=True)
    assert target(a)["signature"] == target(b)["signature"]
    assert target(a)["review_signature"] != target(b)["review_signature"]


def test_arbitrary_paths_symlinks_and_missing_checksums_are_not_read_as_valid_media(film,tmp_path):
    root,project = film
    candidate = project["shots"][0]["video"]
    with pytest.raises(ValueError): check_receipt(root,{"path":"/etc/passwd"})
    with pytest.raises(ValueError): check_receipt(root,{"path":"production/../../private.txt"})
    with pytest.raises(ValueError): check_receipt(root,{"path":candidate["path"]})
    path = root/candidate["path"]
    private = root.parent/(root.name+"-outside.mp4");private.write_bytes(path.read_bytes());path.unlink();path.symlink_to(private)
    with pytest.raises(ValueError): check_receipt(root,candidate)


@pytest.mark.parametrize("invalid",["1\n00:00:00,000 --> 00:00:00,000\nx", "1\n00:99:00,000 --> 00:99:00,100\nx", "broken", "2\n00:00:00,000 --> 00:00:00,100\nx"])
def test_malformed_subtitles_do_not_pass(invalid):
    with pytest.raises(ValueError): parse_srt(invalid,2)


def test_temporal_sampling_uses_eight_real_timestamps_and_cut_neighbors(film):
    root,project = film
    samples,metadata = sampling.sample_video(root/project["shots"][0]["video"]["path"],cuts=[.5])
    assert len(samples) >= 8 and len(samples) > 3
    assert metadata["decoded_frames"] == 24
    assert metadata["timestamps"] == sorted(set(metadata["timestamps"]))
    assert samples[0]["frame_index"] == 0 and samples[-1]["frame_index"] == 23
    assert any(abs(value-.5)<.09 for value in metadata["timestamps"])


@pytest.mark.parametrize("duration,count",[(float('nan'),8),(0,8),(181,8),(1,3),(1,True),(1,17)])
def test_sampling_bounds_are_validated(duration,count):
    with pytest.raises(ValueError): sampling.sample_times(duration,count)


def test_blind_cer_normalization_and_homophones_remain_separate():
    assert compare_transcript("後臺。","后台")["status"] == "exact_match"
    assert compare_transcript("权力","权利")["status"] == "homophone_match"
    value = compare_transcript("请把书给我。","请把伞给我。")
    assert value["character_error_rate"] > 0 and value["phonetic_error_rate"] > 0 and value["status"] == "review_needed"
    with pytest.raises(ValueError): compare_transcript("。","")


def test_asr_wiring_reuses_current_checksum_and_never_uses_hotwords(film):
    root,project = film
    calls = []
    class MockAsr:
        def transcribe(self,path,**options):
            calls.append(options)
            text = "你好" if len(calls)==1 else "再见"
            return iter([SimpleNamespace(start=0,end=.5,text=text,avg_logprob=-.2,no_speech_prob=.01)]),None
    values = external.asr_checks(project,root,transcriber=MockAsr())
    assert len(values)==2 and all(value["status"] == "pass" for value in values)
    assert all(value["hotwords"] is None and value["condition_on_previous_text"] is False for value in calls)
    project["assets"]["audio"]["lines"][0]["text"] = "旧台词"
    values = external.asr_checks(project,root,transcriber=MockAsr())
    assert values[0]["status"] == "unavailable"
    with pytest.raises((FileNotFoundError,ValueError)):
        external.asr_checks(project,root,model_dir=root/"missing-model")


def test_vlm_uses_temporal_evidence_cross_shot_roles_and_actual_reference_images(film,tmp_path,monkeypatch):
    root,project = film
    calls = []
    def request(config,role,system,payload,schema,record_dir,**kwargs):
        calls.append({"schema":schema,"content":kwargs["content"]})
        if 'checks' in schema["properties"]:
            ids = schema["properties"]["checks"]["properties"]["identity"]["properties"]["evidence_ids"]["items"]["enum"]
            return {"checks":{name:{"status":"fail" if name=='props' else "uncertain" if name=='causality' else "pass", "reason":"可见样本证据说明", "evidence_ids":[ids[0]]} for name in ['identity','props','causality']}}
        return {"status":"pass","score":80,"summary":"模拟评估结果，不是实际模型推理","issues":[]}
    monkeypatch.setattr(llm,"_request",request)
    config = {"llm":{"model":"mock-vlm","vision":True},"quality":{"temporal_samples":8}}
    values = semantic.evaluate_semantics(project,root,config,tmp_path/"observations")
    assert len(calls) == 3
    assert sum(item['type']=='image_url' for item in calls[0]["content"]) >= 9
    assert any('REFERENCE:lead' in item.get('text','') for item in calls[-1]["content"])
    assert any(value["category"]=='props' and value["status"]=='fail' for value in values)
    assert any(value["category"]=='causality' and value["status"]=='review_needed' for value in values)
    assert values[0]["metrics"]["sampled_frames"] >= 8
    assert 'config_revision' in values[0]["metrics"]


def test_cross_shot_pairs_include_nonadjacent_same_character():
    project = {"episode":{"cast":{"A":"adult","B":"adult"}},"shots":[{"id":"S01","characters":["A"]},{"id":"S02","characters":["B"]},{"id":"S03","characters":["A"]}]}
    assert ("S01","S03") in semantic.pairs_for(project)


def test_fabricated_vlm_evidence_never_becomes_pass(film,tmp_path,monkeypatch):
    root,project = film
    monkeypatch.setattr(llm,"_request",lambda *args,**kwargs:{"checks":{key:{"status":"pass","reason":"凭空捏造的证据","evidence_ids":["not-an-image"]} for key in ['identity','props','causality']}})
    values = semantic.evaluate_semantics(project,root,{"llm":{"model":"mock","vision":True}},tmp_path/"audit")
    assert any(value["source"]=='vlm' and value["status"]=='unavailable' for value in values)
    assert not any(value["source"]=='vlm' and value["status"]=='pass' for value in values)


def test_vbench_score_import_is_bound_to_media_and_not_artistic_approval(film):
    root,project = film
    report = ev.evaluate(project,root)
    document = {"tool":"VBench","tool_revision":"fixed-offline-checkout","protocol":"custom_input","results":[{"shot_id":"S01","media_sha256":report["media"]["S01"]["sha256"],"scores":{"subject_consistency":.98}}]}
    result = external.vbench_checks(document,report["media"])
    assert result[0]["status"]=='review_needed' and result[0]["metrics"]["not_an_artistic_guarantee"] is True
    bad=copy.deepcopy(document);bad["results"][0]["media_sha256"]='old'
    with pytest.raises(ValueError): external.vbench_checks(bad,report["media"])
    bad=copy.deepcopy(document);bad["results"][0]["scores"]["subject_consistency"]=float('nan')
    with pytest.raises(ValueError): external.vbench_checks(bad,report["media"])


def test_human_review_must_match_media_and_each_dimension(film):
    root,project = film
    report = ev.evaluate(project,root)
    review = {"schema":"ai-studio.human-review.v1","project_id":project["id"],"production_revision":2,"media_signature":report["media_signature"],
              "reviewer":"fixture producer","reviewed_at":"2026-10-13T12:00:00Z","decision":"approve",
              "dimensions":{name:{"status":"pass","evidence":["fixture timecode 00:00–00:02"],"notes":"Mock evidence, not actual artistic assessment"} for name in report["dimensions"]}}
    result = ev.evaluate(project,root,human=review)
    assert result["verdict"]=='human_approved' and result["artistic_quality_certified"] is False
    review["media_signature"]='stale'
    with pytest.raises(ValueError,match="another project"):
        ev.evaluate(project,root,human=review)


def test_protocol_and_profile_must_match_for_regression_comparison(film):
    root,project = film
    before=ev.evaluate(project,root);after=copy.deepcopy(before)
    after["checks"][0]["status"]='fail'
    assert ev.compare_reports(after,before)["changes"]
    after["golden_signature"]='different-protocol'
    assert ev.compare_reports(after,before)["comparable"] is False


def test_database_evaluation_reads_without_migration_or_snapshot_changes(film,tmp_path,monkeypatch):
    root,project = film
    monkeypatch.setattr(store,"DATA",tmp_path/"db");monkeypatch.setattr(store,"DATABASE",tmp_path/"db/studio.sqlite3")
    store.initialize();p=store.create({"brief":"评估读取不应改变任何生产记录"})
    with store.connection() as db: before=db.execute('SELECT snapshot FROM projects WHERE id=?',(p['id'],)).fetchone()[0]
    read=database_project(store.DATABASE,p['id'])
    assert read['revision']==p['revision']
    with store.connection() as db: assert db.execute('SELECT snapshot FROM projects WHERE id=?',(p['id'],)).fetchone()[0]==before
    with pytest.raises(FileNotFoundError): database_project(tmp_path/'missing.sqlite3',p['id'])
    assert not (tmp_path/'missing.sqlite3').exists()


def test_cli_generates_reports_but_requires_explicit_model_confirmation(film,tmp_path):
    root,project=film
    snapshot=tmp_path/'project.json';snapshot.write_text(json.dumps(project))
    out=tmp_path/'cli-report'
    command=[sys.executable,'-m','factory.evaluation','--project-json',str(snapshot),'--root',str(root),'--output',str(out)]
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert ev.read_json(out/'report.json')['verdict']=='human_review_required'
    result=subprocess.run(command+['--vlm-config',str(tmp_path/'no-config.json')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
    assert result.returncode!=0 and 'confirm-vlm-requests' in result.stderr

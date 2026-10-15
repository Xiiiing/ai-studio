"""GPU API contracts and recovery over loopback, with every local GPU path forbidden."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import wave

import pytest

from factory import gpu_client as gc, scheduler, speech
from factory.scheduler import Budget, ProductionNeedsDecision, ProductionPaused, Scheduler
from studio import core


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sources(lines, voices):
    result={}
    for line in lines:
        normalized={"id":line["id"],"role":line["role"],"text":line["text"].strip(),
                    "speaker":voices[line["role"]]["speaker"],"language":"Chinese",
                    "emotion":line.get("emotion",""),"instruct":""}
        source={"line":normalized,"settings":{"dtype":"float32"},"seed":42}
        digest=hashlib.sha256(encoded(source)).hexdigest()
        model_state={"receipt_sha256":"a"*64,"files":{"model.safetensors":{"size":100,"mtime_ns":1}}}
        key=core.fingerprint({"source_sha256":digest,"model_state":model_state,"schema":2})
        result[line["id"]]={"source":source,"source_sha256":digest,"key":key,"model_state":model_state}
    return result


@pytest.fixture
def service(tmp_path,monkeypatch):
    for module in (gc,speech,core):
        monkeypatch.setattr(module,"ROOT",tmp_path)
    def forbidden(*args,**kwargs):
        raise AssertionError("cami must not execute or inspect local GPU services/models")
    monkeypatch.setattr(scheduler,"gpu_snapshot",forbidden)
    monkeypatch.setattr(scheduler.FactoryComfyWorker,"_start",forbidden)
    monkeypatch.setattr(scheduler.GpuLease,"__enter__",forbidden)
    monkeypatch.setattr(speech.subprocess,"run",forbidden)
    monkeypatch.setattr(speech.importlib.util,"spec_from_file_location",forbidden)
    state={"jobs":{},"posts":[],"gets":[],"artifact_gets":[],"drop_post":False,"corrupt":False,"status":"completed","gpu_seconds":7.25}
    def complete(payload):
        ident=payload["request_id"]
        receipt={"gpu_seconds":state["gpu_seconds"],"elapsed_seconds":99,"execution_host":"wkp-test","prompt_id":"owned-prompt"}
        if payload["kind"]=="comfy":
            blobs=[b"verified image bytes"]
            result={"receipt":receipt}
        else:
            expected=sources(payload["lines"],payload["voices"])
            records,blobs={},[]
            for line in payload["lines"]:
                output=io.BytesIO()
                with wave.open(output,"wb") as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b"\x00\x00"*24000)
                audio=output.getvalue()
                meta={"id":line["id"],"role":line["role"],"text":line["text"],"source":expected[line["id"]]["source"],
                      "source_sha256":expected[line["id"]]["source_sha256"],"wav":"/root/wkp_gateway/server.wav",
                      "quality":{"wav_sha256":hashlib.sha256(audio).hexdigest(),"sample_rate":24000,"channels":1,"duration_seconds":1.0}}
                records[line["id"]]={"path":"/root/wkp_gateway/server.wav","metadata_path":"/root/wkp_gateway/server.json",
                    "wave_artifact_index":len(blobs),"metadata_artifact_index":len(blobs)+1}
                blobs.extend([audio,encoded(meta)])
            result={"receipt":receipt,"expected":expected,"records":records}
        result["artifacts"]=[{"index":i,"name":str(i)+".bin","sha256":hashlib.sha256(blob).hexdigest(),"bytes":len(blob)} for i,blob in enumerate(blobs)]
        return {"id":ident,"status":state["status"],"result":result},blobs
    state["make_result"]=complete
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def answer(self,value,status=200):
            data=json.dumps(value).encode()
            self.send_response(status);self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path=="/inference/tts/sources":
                self.answer({"expected":sources(payload["lines"],payload["voices"])});return
            assert self.path=="/inference/jobs"
            state["posts"].append(copy.deepcopy(payload))
            ident=payload["request_id"]
            if ident not in state["jobs"]:
                state["jobs"][ident]=complete(payload)
            if state["drop_post"]:
                state["drop_post"]=False
                self.close_connection=True
                return
            self.answer(state["jobs"][ident][0])
        def do_GET(self):
            path=self.path.removeprefix("/inference/jobs/")
            if "/artifacts/" in path:
                ident,index=path.split("/artifacts/")
                state["artifact_gets"].append((ident,int(index)))
                blob=state["jobs"][ident][1][int(index)]
                if state["corrupt"]:blob=b"x"*len(blob)
                self.send_response(200);self.send_header("Content-Length",str(len(blob)));self.end_headers();self.wfile.write(blob);return
            state["gets"].append(path)
            if path not in state["jobs"]:
                self.answer({"error":"missing"},404);return
            self.answer(state["jobs"][path][0])
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    config={"gpu_service":{"base_url":f"http://127.0.0.1:{server.server_port}/inference","poll_seconds":.01},"max_wall_seconds":60,"max_gpu_seconds":100}
    yield tmp_path,config,state
    server.shutdown();server.server_close();thread.join(timeout=2)


def graph(name=None):
    return {"1":{"class_type":"LoadImage","inputs":{"image":name}}} if name else {"1":{"class_type":"SaveImage","inputs":{}}}


def test_scheduler_remote_worker_never_probes_or_leases_local_gpu_and_only_charges_receipt(service):
    root,config,state=service
    budget=Budget(config)
    events=[]
    with Scheduler(config,budget,events.append).worker("image") as worker:
        source=root/"input.png";source.write_bytes(b"safe local reference")
        name=worker.copy_input(source)
        assert name==hashlib.sha256(source.read_bytes()).hexdigest()+".png"
        output=worker.execute(graph(name),root/"production/take_001","image")
    assert output.read_bytes()==b"verified image bytes"
    assert budget.snapshot()["gpu_seconds"]==7.25 and budget.snapshot()["active_gpu_leases"]==0
    assert events[-1]["execution_host"]=="wkp-test"
    result=core.read_json(output.parent/"result.json")
    assert result["execution_host"]=="wkp-test" and result["origin"]=="remote_gpu_inference"
    assert state["posts"][0]["kind"]=="comfy" and "project_id" not in state["posts"][0]


def test_accepted_post_disconnect_recovers_same_job_and_does_not_submit_new_take(service):
    root,config,state=service
    dest=root/"production/take_001"
    budget=Budget(config)
    state["drop_post"]=True
    with Scheduler(config,budget).worker("first") as worker:
        with pytest.raises(ProductionPaused,match="saved submission"):
            worker.execute(graph(),dest,"image")
    assert len(state["posts"])==1
    submission=core.read_json(dest/"submission.json")
    output=gc.resume_remote_comfy(config,dest,budget)
    assert output.exists() and len(state["posts"])==1
    assert submission["gpu_service_job_id"] in state["gets"]
    assert budget.snapshot()["gpu_seconds"]==7.25
    # Reopened cami budgets and repeated downloads do not bill this job twice.
    reopened=Budget(config,budget.snapshot())
    gc.resume_remote_comfy(config,dest,reopened)
    assert reopened.snapshot()["gpu_seconds"]==7.25


def test_changed_inputs_cannot_replace_an_uncertain_submission(service):
    root,config,state=service
    state["drop_post"]=True
    budget=Budget(config)
    with Scheduler(config,budget).worker("first") as worker:
        with pytest.raises(ProductionPaused):worker.execute(graph(),root/"take","image")
        with pytest.raises(ProductionNeedsDecision,match="inputs changed"):
            worker.execute({"2":{"class_type":"SaveImage","inputs":{}}},root/"take","image")
    assert len(state["posts"])==1


def test_integrity_failure_preserves_completed_job_for_redownload(service):
    root,config,state=service
    state["corrupt"]=True
    budget=Budget(config)
    with Scheduler(config,budget).worker("image") as worker:
        with pytest.raises(ProductionNeedsDecision,match="integrity"):
            worker.execute(graph(),root/"take","image")
        assert not (root/"take/result.json").exists()
        state["corrupt"]=False
        path=worker.execute(graph(),root/"take","image")
    assert path.read_bytes()==b"verified image bytes" and len(state["posts"])==1
    assert budget.snapshot()["gpu_seconds"]==7.25


@pytest.mark.parametrize("status,exception",[("failed",RuntimeError),("unresolved",ProductionNeedsDecision)])
def test_terminal_failure_and_uncertain_server_restart_are_not_automatic_resubmissions(service,status,exception):
    root,config,state=service
    state["status"]=status
    budget=Budget(config)
    with Scheduler(config,budget).worker("image") as worker:
        for _ in range(2):
            with pytest.raises(exception):worker.execute(graph(),root/"take","image")
    assert len(state["posts"])==1
    assert budget.snapshot()["gpu_seconds"]==7.25


def test_cached_result_bytes_are_checked_before_reuse(service):
    root,config,state=service
    budget=Budget(config)
    with Scheduler(config,budget).worker("image") as worker:
        path=worker.execute(graph(),root/"take","image")
        path.write_bytes(b"tampered")
        with pytest.raises(ProductionNeedsDecision,match="Cached GPU artifact"):
            worker.execute(graph(),root/"take","image")
    assert len(state["posts"])==1


@pytest.mark.parametrize("url",["http://172.20.114.10:8790/inference","https://user:secret@172.20.114.10/inference","https://172.20.114.10/inference?token=x","file:///tmp/a"])
def test_service_transport_rejects_unprotected_remote_endpoints_and_embedded_secrets(url):
    with pytest.raises(ValueError):gc.GPUClient({"base_url":url})


def test_remote_https_requires_private_absolute_tls_files():
    with pytest.raises(ValueError,match="absolute private"):
        gc.GPUClient({"base_url":"https://172.20.114.10:8790/inference","ca_file":"relative.pem"})


class SpeechSession(speech.SpeechMixin):
    def __init__(self,root,config):
        self.config=config;self.folder=root/"production/factory-012345abcdef";self.project={};self.production={};self.control=None
        self.budget=Budget(config);self.scheduler=Scheduler(config,self.budget)
        self.episode={};self.cast={"female":"adult woman"}
        self.shots=[{"id":"S01","dialogue":[{"id":"S01_L1","role":"female","text":"钥匙还给你","emotion":"平静","start":.2}]}]
        self.events=[];self.assets={}
    def boundary(self,gpu=False):self.budget.check(require_gpu=gpu)
    def emit(self,phase=None,**patch):self.events.append((phase,patch))
    def receipt_path(self,record):return core.within(gc.ROOT,record["path"])


def test_remote_tts_preserves_per_line_sources_and_hash_cache_without_local_model_import(service):
    root,config,state=service
    core.write_json(root/"audio/voices.json",{"female":{"speaker":"Serena","language":"Chinese"}})
    session=SpeechSession(root,config)
    mapping=session._voice()
    assert mapping["S01_L1"].exists() and len(state["posts"])==1
    record=session.assets["audio"]["lines"][0]
    assert record["execution_host"]=="wkp-test" and record["source_signature"]
    assert not record["path"].startswith("/root/") and not record["metadata_path"].startswith("/root/")
    meta=core.read_json(root/record["metadata_path"])
    assert meta["wav"]==str(mapping["S01_L1"].resolve())
    assert meta["quality"]["wav_sha256"]==core.sha256(mapping["S01_L1"])
    session._voice()
    assert len(state["posts"])==1 and session.budget.snapshot()["gpu_seconds"]==7.25
    payload=state["posts"][0]
    assert set(payload)=={"request_id","kind","lines","voices","options","timeout_seconds","max_gpu_seconds"}
    assert set(payload["options"])=={"tts_dtype","tts_seed","tts_temperature","tts_max_new_tokens"}


def test_remote_tts_disconnect_resumes_the_same_attempt(service):
    root,config,state=service
    core.write_json(root/"audio/voices.json",{"female":{"speaker":"Serena"}})
    state["drop_post"]=True
    session=SpeechSession(root,config)
    with pytest.raises(ProductionPaused):session._voice()
    session._voice()
    assert len(state["posts"])==1
    attempts=core.read_json(next(session.folder.glob("audio/tts/batches/*/attempts.json")))
    assert len(attempts)==1 and attempts[0]["state"]=="completed"


def test_remote_configuration_forbids_a_local_audio_lease(service):
    _,config,_=service
    with pytest.raises(ProductionNeedsDecision,match="job API"):
        with Scheduler(config,Budget(config)).audio_lease():pass


def test_client_matches_real_inference_store_and_http_contract(service):
    from factory import gpu_service
    from studio.graphs import flux_image
    root,config,_=service
    class Engine:
        calls=0
        def execute(self,payload,folder):
            self.calls+=1
            assert core.read_json(folder/"job.json")["status"]=="running"
            target=folder/"artifacts/image.png"
            target.parent.mkdir();target.write_bytes(b"owned-server-image")
            return {"receipt":{"execution_host":"real-contract-test","gpu_seconds":2.5},
                "artifacts":[{"index":0,"name":"image.png","bytes":target.stat().st_size,"sha256":core.sha256(target)}]}
    engine=Engine()
    store=gpu_service.InferenceStore(root/"server-state",engine)
    server=ThreadingHTTPServer(("127.0.0.1",0),gpu_service.Handler)
    server.store=store
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    remote={**config,"gpu_service":{**config["gpu_service"],"base_url":f"http://127.0.0.1:{server.server_port}"}}
    try:
        budget=Budget(remote)
        with Scheduler(remote,budget).worker("contract") as worker:
            target=worker.execute(flux_image("A fictional adult in watercolor.",42),root/"cami/take","image")
            assert target.read_bytes()==b"owned-server-image"
            assert worker.execute(flux_image("A fictional adult in watercolor.",42),root/"cami/take","image")==target
        assert engine.calls==1 and budget.snapshot()["gpu_seconds"]==2.5
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2);store.close()


def test_remote_usage_receipts_are_cumulative_idempotent_and_not_clock_based():
    budget=Budget({"max_wall_seconds":60,"max_gpu_seconds":100})
    budget.record_remote("a"*64,2)
    budget.record_remote("a"*64,2)
    budget.record_remote("a"*64,3)
    assert budget.snapshot()["gpu_seconds"]==3
    assert budget.snapshot()["active_gpu_leases"]==0
    with pytest.raises(ProductionNeedsDecision):budget.record_remote("a"*64,1)
    with pytest.raises(ProductionNeedsDecision):budget.record_remote("b"*64,float("nan"))


def test_upload_paths_cannot_escape_cami_media_root(service,tmp_path):
    _,config,_=service
    outside=tmp_path.parent/(tmp_path.name+"-outside.png")
    outside.write_bytes(b"private")
    try:
        with Scheduler(config,Budget(config)).worker("upload") as worker:
            with pytest.raises(ValueError,match="escapes"):
                worker.copy_input(outside)
    finally:outside.unlink()


def test_remote_scheduler_does_not_require_any_cami_gpu_configuration(service):
    root,config,_=service
    remote={**config,"gpu_cards":[],"image_min_free_mib":0,"comfy_start_port":0}
    budget=Budget(remote)
    scheduler=Scheduler(remote,budget)
    with scheduler.worker("cpu-host") as worker:
        assert worker.execute(graph(),root/"cpu-only/take","image").exists()
    assert "excluding_network_wait" in budget.snapshot()["accounting"]
    with pytest.raises(ProductionNeedsDecision):scheduler.choose_card()


def test_invalid_remote_configuration_never_falls_back_to_local_scheduler():
    with pytest.raises(ValueError,match="explicit endpoint"):
        Scheduler({"gpu_service":{}},Budget({"max_wall_seconds":60,"max_gpu_seconds":100}))


@pytest.mark.parametrize("status",["queued","running","unresolved"])
def test_changed_source_cannot_hide_an_existing_pending_gpu_job(service,status):
    root,config,state=service
    state["drop_post"]=True;state["status"]=status
    folder=root/"asset";take=folder/"take_001"
    core.write_json(take/"attempt.json",{"source_signature":"old"})
    budget=Budget(config)
    with Scheduler(config,budget).worker("old") as worker:
        with pytest.raises(ProductionPaused):worker.execute(graph(),take,"image")
    with pytest.raises(ProductionNeedsDecision,match="older GPU job"):
        gc.guard_remote_submissions(config,folder,budget,current_signature="new")
    assert len(state["posts"])==1
    # The same source is recovered by the existing take, never intercepted as
    # permission to allocate a new take.
    gc.guard_remote_submissions(config,folder,budget,current_signature="old")
    assert len(state["posts"])==1


@pytest.mark.parametrize("status",["completed","failed"])
def test_changed_source_reconciles_terminal_old_job_usage_once_before_new_take(service,status):
    root,config,state=service
    state["drop_post"]=True;state["status"]=status
    folder=root/"asset";take=folder/"take_001"
    core.write_json(take/"attempt.json",{"source_signature":"old"})
    budget=Budget(config)
    with Scheduler(config,budget).worker("old") as worker:
        with pytest.raises(ProductionPaused):worker.execute(graph(),take,"image")
    gc.guard_remote_submissions(config,folder,budget,current_signature="new")
    gc.guard_remote_submissions(config,folder,budget,current_signature="new")
    assert budget.snapshot()["gpu_seconds"]==7.25
    assert core.read_json(take/"submission.json")["state"]==status
    assert len(state["posts"])==1
    state["status"]="completed"
    with Scheduler(config,budget).worker("new") as worker:
        assert worker.execute(graph(),folder/"take_002","image").exists()
    assert len(state["posts"])==2 and budget.snapshot()["gpu_seconds"]==14.5


def test_changed_source_guard_covers_repair_keyframe_jobs(service):
    root,config,state=service
    state["drop_post"]=True;state["status"]="running"
    folder=root/"asset";take=folder/"take_001"
    core.write_json(take/"attempt.json",{"source_signature":"old"})
    budget=Budget(config)
    with Scheduler(config,budget).worker("repair") as worker:
        with pytest.raises(ProductionPaused):worker.execute(graph(),take/"repair_keyframe","image")
    with pytest.raises(ProductionNeedsDecision):gc.guard_remote_submissions(config,folder,budget,current_signature="new")
    gc.guard_remote_submissions(config,folder,budget,current_signature="old")
    assert len(state["posts"])==1


def test_cached_remote_result_recovers_usage_even_if_cami_budget_checkpoint_was_lost(service):
    root,config,state=service
    with Scheduler(config,Budget(config)).worker("first") as worker:
        expected=worker.execute(graph(),root/"take","image")
    recovered=Budget(config)
    with Scheduler(config,recovered).worker("reopen") as worker:
        assert worker.execute(graph(),root/"take","image")==expected
    assert len(state["posts"])==1 and recovered.snapshot()["gpu_seconds"]==7.25


@pytest.mark.parametrize("old_status",["running","unresolved"])
def test_project_fence_blocks_changed_tts_batch_while_prior_gpu_job_is_not_terminal(service,old_status):
    root,config,state=service
    core.write_json(root/"audio/voices.json",{"female":{"speaker":"Serena"}})
    state["drop_post"]=True;state["status"]=old_status
    session=SpeechSession(root,config)
    with pytest.raises(ProductionPaused):session._voice()
    old_submission=next(session.folder.glob("audio/tts/batches/*/remote_attempt_01/submission.json"))
    old_id=core.read_json(old_submission)["gpu_service_job_id"]
    session.shots[0]["dialogue"][0]["text"]="改词后新的对白"
    state["status"]="completed"
    with pytest.raises(ProductionNeedsDecision,match="earlier project GPU job"):
        session._voice()
    assert len(state["posts"])==1
    assert list(session.folder.rglob("submission.json"))==[old_submission]
    assert old_id in state["gets"]


def test_project_fence_reconciles_old_tts_usage_before_allowing_a_new_batch(service):
    root,config,state=service
    core.write_json(root/"audio/voices.json",{"female":{"speaker":"Serena"}})
    state["drop_post"]=True
    session=SpeechSession(root,config)
    with pytest.raises(ProductionPaused):session._voice()
    session.shots[0]["dialogue"][0]["text"]="改词后新的对白"
    session._voice()
    assert len(state["posts"])==2
    assert session.budget.snapshot()["gpu_seconds"]==14.5
    second=state["posts"][1]
    assert second["max_gpu_seconds"]==100-7.25
    assert any(patch.get("remote_inference",{}).get("reason")=="remote_gpu_project_reconciled" for _,patch in session.events)


def test_project_fence_accounts_old_terminal_job_then_stops_at_current_gpu_cap_before_new_intent(service):
    root,config,state=service
    core.write_json(root/"audio/voices.json",{"female":{"speaker":"Serena"}})
    state["drop_post"]=True
    session=SpeechSession(root,config)
    with pytest.raises(ProductionPaused):session._voice()
    # Simulate a cami checkpoint that already contains other usage but lost the
    # old TTS terminal response. Reconciliation must consume the remaining cap.
    session.budget=Budget({"max_wall_seconds":60,"max_gpu_seconds":10},{"gpu_seconds":3})
    session.shots[0]["dialogue"][0]["text"]="改词后新的对白"
    with pytest.raises(scheduler.ProductionBudgetExceeded):session._voice()
    assert session.budget.snapshot()["gpu_seconds"]==10.25
    assert len(state["posts"])==1
    assert len(list(session.folder.rglob("submission.json")))==1


def test_project_fence_covers_old_different_shot_and_repair_before_tts_dispatch(service):
    root,config,state=service
    project=root/"production/factory-012345abcdef"
    old=project/"shots/S01/take_001/repair_keyframe"
    state["drop_post"]=True;state["status"]="running"
    budget=Budget(config)
    with Scheduler(config,budget).worker("repair") as worker:
        with pytest.raises(ProductionPaused):worker.execute(graph(),old,"image")
        with pytest.raises(ProductionNeedsDecision,match="earlier project GPU job"):
            worker.execute(graph(),project/"shots/S02/take_001","image")
    assert len(state["posts"])==1
    assert not (project/"shots/S02/take_001/submission.json").exists()


def test_project_fence_missing_old_identity_does_not_authorize_a_replacement(service):
    root,config,state=service
    project=root/"production/factory-012345abcdef"
    state["drop_post"]=True
    budget=Budget(config)
    with Scheduler(config,budget).worker("first") as worker:
        with pytest.raises(ProductionPaused):worker.execute(graph(),project/"shots/S01/take_001","image")
        state["jobs"].clear()
        with pytest.raises(ProductionNeedsDecision,match="cannot be reconciled"):
            worker.execute(graph(),project/"shots/S02/take_001","image")
    assert len(state["posts"])==1
    assert not (project/"shots/S02/take_001/submission.json").exists()


def test_project_fence_transport_failure_never_creates_a_new_intent(service,monkeypatch):
    root,config,state=service
    project=root/"production/factory-012345abcdef"
    state["drop_post"]=True
    budget=Budget(config)
    with Scheduler(config,budget).worker("first") as worker:
        with pytest.raises(ProductionPaused):worker.execute(graph(),project/"shots/S01/take_001","image")
        def offline(*args,**kwargs):
            raise ProductionPaused("offline","remote_gpu_unavailable")
        monkeypatch.setattr(gc.GPUClient,"request",offline)
        with pytest.raises(ProductionPaused):worker.execute(graph(),project/"shots/S02/take_001","image")
    assert len(state["posts"])==1
    assert not (project/"shots/S02/take_001/submission.json").exists()


def test_legacy_voice_map_aliases_are_not_sent_to_real_http_speech_contract(service):
    """Use the bundled aliases and the real server validator, not a permissive mock."""
    from factory import gpu_service
    root,config,state=service
    bundled=json.loads((Path(__file__).resolve().parents[1]/"audio/voices.json").read_text())
    assert {"女","女主","男","男主","female","male"} <= bundled.keys()
    core.write_json(root/"audio/voices.json",bundled)
    class Engine:
        def __init__(self):
            self.source_requests=[];self.jobs=[]
        def sources(self,payload,folder):
            self.source_requests.append(copy.deepcopy(payload))
            return {"expected":sources(payload["lines"],payload["voices"])}
        def execute(self,payload,folder):
            self.jobs.append(copy.deepcopy(payload))
            answer,blobs=state["make_result"](payload)
            artifacts=folder/"artifacts";artifacts.mkdir()
            for record,blob in zip(answer["result"]["artifacts"],blobs):
                (artifacts/record["name"]).write_bytes(blob)
            return answer["result"]
    engine=Engine()
    store=gpu_service.InferenceStore(root/"real-tts-service",engine)
    server=ThreadingHTTPServer(("127.0.0.1",0),gpu_service.Handler)
    server.store=store
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    remote={**config,"gpu_service":{**config["gpu_service"],"base_url":f"http://127.0.0.1:{server.server_port}"}}
    try:
        session=SpeechSession(root,remote)
        assert not session.project.get("production_profile")
        result=session._voice()
        assert result["S01_L1"].is_file()
        assert len(engine.jobs)==1 and len(engine.source_requests)==1
        for payload in [*engine.source_requests,*engine.jobs]:
            assert set(payload["voices"])=={"female"}
            assert payload["voices"]["female"]==bundled["female"]
        session._voice()
        assert len(engine.jobs)==1
        assert session.budget.snapshot()["gpu_seconds"]==7.25
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2);store.close()

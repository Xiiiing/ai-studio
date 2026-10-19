"""Durable low-level GPU API client; all workflow ownership stays on cami.

Only Comfy graphs, verified input bytes and TTS requests cross this boundary.
Submission identities are persisted before network I/O and never replaced after
an uncertain response. GPU accounting uses server receipts, not polling time.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from studio.core import ROOT, fingerprint, read_json, sha256, within, write_json
from .scheduler import ProductionBudgetExceeded, ProductionNeedsDecision, ProductionPaused

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProductionNeedsDecision("GPU service redirects are not accepted", "remote_gpu_redirect")


class GPUClient:
    def __init__(self, config):
        if not isinstance(config, dict):
            raise ValueError("gpu_service must be an object")
        self.config = copy.deepcopy(config)
        base = config.get("base_url", "")
        if not isinstance(base, str):
            raise ValueError("GPU service URL must be text")
        parsed = urllib.parse.urlsplit(base)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in {"http", "https"} or (parsed.scheme == "http" and not loopback)):
            raise ValueError("GPU service requires HTTPS with an owned endpoint; HTTP is loopback-only")
        self.base = base.rstrip("/")
        self.execution_host = parsed.hostname
        context = None
        if parsed.scheme == "https":
            paths = {}
            for key in ("ca_file", "client_cert", "client_key"):
                raw = config.get(key)
                if not isinstance(raw, str) or not Path(raw).is_absolute() or not Path(raw).is_file():
                    raise ValueError("GPU service TLS files must be existing absolute private paths")
                paths[key] = raw
            if Path(paths["client_key"]).stat().st_mode & 0o077:
                raise ValueError("GPU service client key must not be accessible to other users")
            context = ssl.create_default_context(cafile=paths["ca_file"])
            context.load_cert_chain(paths["client_cert"], paths["client_key"])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                                   urllib.request.HTTPSHandler(context=context))
        self.poll_seconds = float(config.get("poll_seconds", 2))
        self.request_timeout = float(config.get("request_timeout_seconds", 30))
        if not math.isfinite(self.poll_seconds) or not .01 <= self.poll_seconds <= 30:
            raise ValueError("GPU service poll interval must be bounded")
        if not math.isfinite(self.request_timeout) or not .05 <= self.request_timeout <= 120:
            raise ValueError("GPU service request timeout must be bounded")

    def request(self, route, payload=None, *, timeout=None, missing=False):
        raw = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if raw is not None and len(raw)>32*1024*1024:
            raise ValueError("GPU request exceeded the bounded input limit")
        request = urllib.request.Request(self.base + route, data=raw,
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=min(self.request_timeout, timeout or self.request_timeout)) as response:
                body = response.read(MAX_JSON_BYTES + 1)
                if len(body) > MAX_JSON_BYTES:
                    raise ValueError("GPU response exceeded the bounded JSON limit")
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError("GPU response must be an object")
                return value
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing:
                return None
            if exc.code in {400, 401, 403, 409, 413, 422}:
                raise ProductionNeedsDecision("GPU service rejected the request", "remote_gpu_rejected",
                                              {"http_status": exc.code}) from exc
            raise ProductionPaused("GPU service is unavailable; saved submission retained", "remote_gpu_unavailable") from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise ProductionPaused("GPU service is unavailable; saved submission retained", "remote_gpu_unavailable") from exc
        except (ValueError, UnicodeError) as exc:
            raise ProductionNeedsDecision("GPU service returned invalid response data", "remote_gpu_receipt_invalid") from exc

    def sources(self, lines, voices, options, project_profile=None):
        payload = {"lines": lines, "voices": voices, "options": options}
        if project_profile is not None:
            payload["project_profile"] = project_profile
        answer = self.request("/tts/sources", payload)
        expected = answer.get("expected")
        if not isinstance(expected, dict) or set(expected) != {line["id"] for line in lines}:
            raise ProductionNeedsDecision("GPU service returned mismatched speech sources", "remote_gpu_receipt_invalid")
        for line in lines:
            record = expected[line["id"]]
            if (not isinstance(record, dict) or not isinstance(record.get("source"), dict)
                    or not re.fullmatch(r"[a-f0-9]{64}", str(record.get("source_sha256", "")))
                    or not re.fullmatch(r"[a-f0-9]{64}", str(record.get("key", "")))
                    or record["source"].get("line", {}).get("id") != line["id"]
                    or record["source"]["line"].get("text") != line.get("text", "").strip()
                    or record["source"]["line"].get("role") != line.get("role")
                    or hashlib.sha256(json.dumps(record["source"],ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()).hexdigest()!=record["source_sha256"]):
                raise ProductionNeedsDecision("GPU speech provenance is invalid", "remote_gpu_receipt_invalid")
        return expected

    def require_h3(self, profile=None):
        from studio.h3 import backend_ready
        observed = self.request("/health")
        backend = observed.get("video_backends", {}).get("comfyui_h3", {})
        if not backend_ready(backend, observed.get("gpu_cards", []), profile):
            raise ProductionNeedsDecision("Wkp 尚未提供已核实的 ComfyUI / MiniMax H3 双卡链路；请更新推理服务，不能回退到 Wan", "remote_h3_unavailable")
        return backend

    def run(self, workload, destination, budget, control=None, event_callback=None, timeout=7200):
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        submission_path = destination / "submission.json"
        retained_submission = submission_path.exists()
        if (not submission_path.exists() and workload.get("kind") == "comfy" and
                any(n.get("class_type") == "MiniMaxH3ImageToVideo" for n in workload.get("graph", {}).values())):
            from studio.h3 import graph_recipe
            selected = graph_recipe(workload["graph"])
            if selected["quality"] == "production":
                self.require_h3(selected)
            else:
                self.require_h3()
        identity = fingerprint({"workload": workload, "destination": str(destination.resolve()), "service": self.base})
        if submission_path.exists():
            submission = read_json(submission_path)
            if submission.get("request_identity") != identity:
                raise ProductionNeedsDecision("GPU inputs changed for an existing take", "remote_gpu_submission_changed")
            payload = submission["request"]
            request_id = submission["gpu_service_job_id"]
        else:
            self._fence_project_submission(destination,budget,control,event_callback)
            budget.check(control, require_gpu=True)
            wall_limit=min(21600,float(timeout),budget.timeout(timeout))
            gpu_limit=min(21600,budget.max_gpu-budget.snapshot()["gpu_seconds"])
            if wall_limit<1 or gpu_limit<1:
                raise ProductionBudgetExceeded(details=budget.snapshot())
            payload = {**copy.deepcopy(workload), "request_id": identity,
                       "timeout_seconds": wall_limit, "max_gpu_seconds": gpu_limit}
            request_id = identity
            submission = {"state": "dispatching", "request_identity": identity, "gpu_service_job_id": request_id,
                          "execution_host": self.execution_host, "submitted_at": time.time(), "request": payload}
            # An uncertain POST always reuses this exact body and identifier.
            write_json(submission_path, submission)
        if not re.fullmatch(r"[a-f0-9]{64}", request_id):
            raise ProductionNeedsDecision("GPU submission identity is invalid", "remote_gpu_receipt_invalid")
        blocked = None
        try:
            budget.check(control)
            remaining = budget.timeout(timeout)
        except (ProductionBudgetExceeded, ProductionPaused) as exc:
            if not retained_submission:
                raise
            blocked, remaining = exc, 0
        deadline = time.monotonic() + remaining
        answer = self.request("/jobs/" + request_id,
                              timeout=min(10, self.request_timeout) if blocked else budget.timeout(self.request_timeout), missing=True)
        if answer is None:
            if blocked or retained_submission and submission.get('state') != 'dispatching':
                raise ProductionNeedsDecision('Saved GPU submission is missing during read-only reconciliation; no resubmission permitted',
                                              'remote_gpu_unresolved', {'job_id': request_id})
            answer = self.request("/jobs", payload, timeout=budget.timeout(self.request_timeout))
        while True:
            if answer.get("id") != request_id:
                raise ProductionNeedsDecision("GPU response does not match the saved submission", "remote_gpu_receipt_invalid")
            status = answer.get("status")
            submission.update(state=status, last_seen_at=time.time())
            write_json(submission_path, submission)
            result = answer.get("result") or {}
            receipt = result.get("receipt") or answer.get("receipt")
            if isinstance(receipt, dict) and "gpu_seconds" in receipt:
                budget.record_remote(request_id, receipt["gpu_seconds"])
            if event_callback:
                event_callback({"status": "ready" if status == "completed" else "waiting", "reason": "remote_gpu_" + str(status),
                                "stage": workload["kind"], "job_id": request_id, "execution_host": receipt.get("execution_host",self.execution_host) if isinstance(receipt,dict) else self.execution_host,
                                "gpu_seconds": receipt.get("gpu_seconds") if isinstance(receipt, dict) else None})
            if status == "completed":
                if not isinstance(receipt, dict) or "gpu_seconds" not in receipt:
                    raise ProductionNeedsDecision("GPU completion is missing usage provenance", "remote_gpu_receipt_invalid")
                write_json(destination / "remote_result.json", answer)
                return answer
            if status in {"failed", "unresolved"}:
                write_json(destination / "remote_result.json", answer)
                if status == "unresolved":
                    raise ProductionNeedsDecision("The previous remote GPU execution needs reconciliation; it will not be resubmitted", "remote_gpu_unresolved", {"job_id": request_id})
                write_json(destination / "submission_error.json", {"job_id": request_id, "status": status})
                raise RuntimeError("Remote GPU execution failed; its terminal receipt was retained")
            if status not in {"queued", "running"}:
                raise ProductionNeedsDecision("Unknown GPU job state", "remote_gpu_receipt_invalid")
            if blocked:
                raise blocked
            budget.check(control)
            if time.monotonic() >= deadline:
                raise ProductionPaused("GPU job is still pending; resume the saved job", "remote_gpu_wait", {"job_id": request_id})
            until = min(deadline, time.monotonic() + self.poll_seconds)
            while time.monotonic() < until:
                budget.check(control)
                time.sleep(min(.25, max(0, until - time.monotonic())))
            answer = self.request("/jobs/" + request_id, timeout=budget.timeout(self.request_timeout))

    def _fence_project_submission(self,destination,budget,control,event_callback):
        """Reconcile all older project GPU effects before a brand-new intent.

        TTS wording changes and cross-shot/phase transitions create different
        destination folders, so a per-asset attempt filter cannot own this fence.
        Existing intents bypass it and continue querying their saved identity.
        """
        production=(ROOT/"production").resolve()
        try:
            relative=Path(destination).resolve().relative_to(production)
        except ValueError:
            return
        if not relative.parts or not re.fullmatch(r"factory-[a-f0-9]{12}",relative.parts[0]):
            return
        project=production/relative.parts[0]
        seen=set()
        for path in sorted(project.rglob("submission.json")):
            budget.check(control)
            submission=read_json(path)
            ident=submission.get("gpu_service_job_id")
            if not ident:
                continue
            if not isinstance(ident,str) or not re.fullmatch(r"[a-f0-9]{64}",ident):
                raise ProductionNeedsDecision("A project GPU submission has invalid identity","remote_gpu_receipt_invalid")
            if ident in seen:
                continue
            seen.add(ident)
            terminal_path=path.parent/"remote_result.json"
            answer=read_json(terminal_path) if terminal_path.exists() else None
            if (not isinstance(answer,dict) or answer.get("id")!=ident
                    or answer.get("status") not in {"completed","failed"}):
                answer=self.request("/jobs/"+ident,timeout=budget.timeout(self.request_timeout),missing=True)
            if answer is None or answer.get("id")!=ident:
                raise ProductionNeedsDecision("An earlier project GPU submission cannot be reconciled; no new inference was submitted", "remote_gpu_unresolved",{"job_id":ident})
            status=answer.get("status")
            receipt=(answer.get("result") or {}).get("receipt") or answer.get("receipt")
            if isinstance(receipt,dict) and "gpu_seconds" in receipt:
                budget.record_remote(ident,receipt["gpu_seconds"])
                if event_callback:
                    event_callback({"status":"ready" if status in {"completed","failed"} else "waiting",
                        "reason":"remote_gpu_project_reconciled","stage":"gpu","job_id":ident,
                        "execution_host":receipt.get("execution_host",self.execution_host),"gpu_seconds":receipt["gpu_seconds"]})
            if status not in {"completed","failed"}:
                raise ProductionNeedsDecision("An earlier project GPU job is pending or unresolved; resume it before starting new inference", "remote_gpu_unresolved",{"job_id":ident,"status":status})
            if not isinstance(receipt,dict) or "gpu_seconds" not in receipt:
                raise ProductionNeedsDecision("An earlier project GPU job lacks usage provenance","remote_gpu_receipt_invalid")
            submission.update(state=status,reconciled_at=time.time())
            write_json(path,submission)
            write_json(terminal_path,answer)
            if status=="failed":
                write_json(path.parent/"submission_error.json",{"job_id":ident,"status":status})

    def download(self, job_id, artifact, target):
        index, size, expected = artifact.get("index"), artifact.get("bytes"), artifact.get("sha256")
        if (type(index) is not int or index < 0 or type(size) is not int or not 0 < size <= MAX_ARTIFACT_BYTES
                or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise ProductionNeedsDecision("GPU artifact metadata is invalid", "remote_gpu_receipt_invalid")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == size and sha256(target) == expected:
            return target
        temp = target.with_name(target.name + ".download")
        digest, downloaded = hashlib.sha256(), 0
        try:
            request = urllib.request.Request(self.base + f"/jobs/{job_id}/artifacts/{index}")
            with self.opener.open(request, timeout=self.request_timeout) as response, temp.open("wb") as output:
                while block := response.read(min(1024 * 1024, size - downloaded + 1)):
                    downloaded += len(block)
                    if downloaded > size:
                        raise ValueError("GPU artifact exceeded its receipt size")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if downloaded != size or digest.hexdigest() != expected:
                raise ValueError("GPU artifact bytes do not match its receipt")
            os.replace(temp, target)
            return target
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise ProductionPaused("GPU artifact download interrupted; saved job retained", "remote_gpu_unavailable") from exc
        except ValueError as exc:
            raise ProductionNeedsDecision("GPU artifact failed integrity verification", "remote_gpu_receipt_invalid") from exc
        finally:
            temp.unlink(missing_ok=True)


def _result_receipt(target, **extra):
    return {"path": str(Path(target).relative_to(ROOT)), "bytes": Path(target).stat().st_size,
            "sha256": sha256(target), **extra}


class RemoteComfyWorker:
    def __init__(self, config, budget, tag, control=None, event_callback=None):
        self.client = GPUClient(config)
        self.budget, self.tag, self.control, self.event_callback = budget, tag, control, event_callback
        self.inputs = {}

    def __enter__(self):
        self.budget.check(self.control, require_gpu=True)
        return self

    def __exit__(self, *args):
        return False

    def copy_input(self, path):
        path = within(ROOT,path)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
            raise ValueError("GPU input must be a bounded local media file")
        with path.open("rb") as source:
            data=source.read(MAX_INPUT_BYTES+1)
        if len(data)>MAX_INPUT_BYTES:
            raise ValueError("GPU input exceeded its bounded size")
        digest = hashlib.sha256(data).hexdigest()
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Unsupported GPU input media type")
        name = digest + suffix
        if len(self.inputs)>=16 and name not in self.inputs:
            raise ValueError("Too many GPU input images")
        self.inputs[name] = {"name": name, "data": base64.b64encode(data).decode("ascii"), "sha256": digest}
        return name

    def execute(self, graph, destination, kind, timeout=7200):
        if kind not in {"image", "video"}:
            raise ValueError("Unknown GPU output kind")
        destination = Path(destination)
        signature = fingerprint(graph)
        result_path = destination / "result.json"
        if result_path.is_file():
            saved = read_json(result_path)
            if saved.get("graph_sha256") != signature:
                raise ProductionNeedsDecision("Changed graph for an existing GPU take", "remote_gpu_submission_changed")
            target = within(ROOT,saved["path"])
            if not target.is_file() or sha256(target) != saved.get("sha256"):
                raise ProductionNeedsDecision("Cached GPU artifact failed verification", "remote_gpu_receipt_invalid")
            if not isinstance(saved.get("gpu_service_job_id"),str) or not re.fullmatch(r"[a-f0-9]{64}",saved["gpu_service_job_id"]):
                raise ProductionNeedsDecision("Cached GPU provenance is invalid", "remote_gpu_receipt_invalid")
            self.budget.record_remote(saved["gpu_service_job_id"],saved.get("gpu_seconds"))
            if self.event_callback:
                self.event_callback({"status":"ready","reason":"remote_gpu_cached","stage":"comfy",
                    "job_id":saved["gpu_service_job_id"],"execution_host":saved.get("execution_host",self.client.execution_host),
                    "gpu_seconds":saved["gpu_seconds"]})
            return target
        typed_image = graph.get("kind") == "qwen_image21"
        required = set(graph["references"]) if typed_image else {node.get("inputs", {}).get("image") for node in graph.values() if node.get("class_type") == "LoadImage"}
        if not required <= self.inputs.keys():
            raise ValueError("GPU graph references media that was not uploaded")
        workload = ({k: v for k, v in graph.items() if k != "references"} | {
            "inputs": [self.inputs[name] for name in graph["references"]]}) if typed_image else {
            "kind": "comfy", "graph": graph, "inputs": [self.inputs[name] for name in sorted(required)], "output_kind": kind}
        if typed_image and kind != "image":
            raise ValueError("Qwen Image requests only produce images")
        write_json(destination / "workflow_api.json", graph)
        answer = self.client.run(workload, destination, self.budget, self.control, self.event_callback, timeout)
        return self._finish(answer, destination, signature, kind)

    def _finish(self, answer, destination, signature, kind):
        result = answer.get("result", {})
        artifacts = result.get("artifacts", [])
        if not isinstance(artifacts, list) or len(artifacts) != 1:
            raise ProductionNeedsDecision("GPU service did not return exactly one media artifact", "remote_gpu_receipt_invalid")
        receipt = result["receipt"]
        graph_path = Path(destination) / "workflow_api.json"
        source = read_json(graph_path) if graph_path.is_file() else {}
        if source.get("kind") == "qwen_image21" and (receipt.get("provider") != "comfyui_qwen_image21" or receipt.get("model") != "Qwen-Image-2.1"):
            raise ProductionNeedsDecision("Image result does not match the submitted model", "remote_gpu_receipt_invalid")
        if graph_path.is_file() and any(isinstance(n, dict) and n.get("class_type") == "MiniMaxH3ImageToVideo" for n in source.values()):
            from studio.h3 import MODEL, graph_recipe
            selected = graph_recipe(read_json(graph_path))
            if (receipt.get("provider") != "comfyui_h3" or receipt.get("model") != MODEL or
                    receipt.get("model_revision") != selected["revision"] or receipt.get("graph_sha256") != signature or
                    (selected["quality"] == "production" and receipt.get("generation_parameters") != selected)):
                raise ProductionNeedsDecision("H3 result does not match the submitted model and workflow", "remote_gpu_receipt_invalid")
        target = self.client.download(answer["id"], artifacts[0], Path(destination) / ("image.png" if kind == "image" else "video.mp4"))
        saved = _result_receipt(target, graph_sha256=signature, prompt_id=receipt.get("prompt_id"),
            gpu_service_job_id=answer["id"], gpu_seconds=receipt["gpu_seconds"], execution_host=receipt.get("execution_host", self.client.execution_host),
            elapsed_seconds=receipt.get("elapsed_seconds"), origin="remote_gpu_inference", provider_receipt=str((Path(destination)/"remote_result.json").relative_to(ROOT)))
        for key in ("provider", "model", "model_revision", "generation_parameters"):
            if key in receipt:
                saved[key] = receipt[key]
        write_json(Path(destination) / "result.json", saved)
        return target


def resume_remote_comfy(config, destination, budget, control=None, event_callback=None):
    """Recover the same take before the production engine considers a new attempt."""
    destination = Path(destination)
    submission = read_json(destination / "submission.json")
    worker = RemoteComfyWorker(config["gpu_service"], budget, "resume", control, event_callback)
    request = submission["request"]
    workload = {key: value for key, value in request.items() if key not in {"request_id", "timeout_seconds", "max_gpu_seconds"}}
    answer = worker.client.run(workload, destination, budget, control, event_callback)
    typed = request.get("kind") == "qwen_image21"
    source = read_json(destination / "workflow_api.json") if typed else request["graph"]
    return worker._finish(answer, destination, fingerprint(source), "image" if typed else request["output_kind"])


def guard_remote_submissions(config, asset_folder, budget, control=None, event_callback=None, current_signature=None):
    """Fence older sources before the engine allocates a replacement take.

    Same-source jobs remain owned by the existing take and its normal resume
    path, including repair keyframes. A changed source cannot hide an in-flight
    job simply because the production attempt filter selects a newer signature.
    """
    if not config.get("gpu_service"):
        return
    client=GPUClient(config["gpu_service"])
    folder=Path(asset_folder)
    paths=sorted(folder.glob("take_*/submission.json"))+sorted(folder.glob("take_*/repair_keyframe/submission.json"))
    for path in paths:
        submission=read_json(path)
        ident=submission.get("gpu_service_job_id")
        if not ident:
            continue
        if not isinstance(ident,str) or not re.fullmatch(r"[a-f0-9]{64}",ident):
            raise ProductionNeedsDecision("An older GPU submission has invalid identity","remote_gpu_receipt_invalid")
        take=path.parent.parent if path.parent.name=="repair_keyframe" else path.parent
        attempt_path=take/"attempt.json"
        attempt=read_json(attempt_path) if attempt_path.exists() else {}
        if current_signature is not None and attempt.get("source_signature")==current_signature:
            continue
        budget.check(control)
        terminal_path=path.parent/"remote_result.json"
        answer=read_json(terminal_path) if terminal_path.exists() else None
        if not isinstance(answer,dict) or answer.get("id")!=ident or answer.get("status") not in {"completed","failed"}:
            answer=client.request("/jobs/"+ident,timeout=budget.timeout(client.request_timeout),missing=True)
        if answer is None:
            # A missing old identity is not proof that an uncertain POST never
            # reached inference. Only its owning take may retry the same POST.
            raise ProductionNeedsDecision("An older GPU request cannot yet be reconciled","remote_gpu_unresolved",{"job_id":ident})
        if answer.get("id")!=ident:
            raise ProductionNeedsDecision("An older GPU response has mismatched identity","remote_gpu_receipt_invalid")
        status=answer.get("status")
        receipt=(answer.get("result") or {}).get("receipt") or answer.get("receipt")
        if isinstance(receipt,dict) and "gpu_seconds" in receipt:
            budget.record_remote(ident,receipt["gpu_seconds"])
        if status not in {"completed","failed"}:
            raise ProductionNeedsDecision("An older GPU job is still pending or unresolved; reconcile it before changing inputs", "remote_gpu_unresolved",{"job_id":ident,"status":status})
        if not isinstance(receipt,dict) or "gpu_seconds" not in receipt:
            raise ProductionNeedsDecision("An older GPU job lacks an accounting receipt","remote_gpu_receipt_invalid")
        submission.update(state=status,reconciled_at=time.time())
        write_json(path,submission)
        write_json(terminal_path,answer)
        if status=="failed":
            write_json(path.parent/"submission_error.json",{"job_id":ident,"status":status})
        if event_callback:
            event_callback({"status":"ready","reason":"remote_gpu_reconciled","stage":"comfy","job_id":ident,
                            "execution_host":receipt.get("execution_host",client.execution_host),"gpu_seconds":receipt["gpu_seconds"]})

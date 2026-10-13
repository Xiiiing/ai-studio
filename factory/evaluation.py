"""Repeatable, read-only film evaluation; explicitly opt in to any model inference."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import tempfile
import time
import uuid

from studio.core import fingerprint, sha256, within
from .evaluation_io import database_project, export_report, finite, read_json, row
from .evaluation_media import deterministic
from .evaluation_external import asr_checks, human_checks, vbench_checks

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evaluation/golden.json"


def observations(project, root):
    """Actual recorded identities only; current settings never fill history gaps."""
    values, truncated = [], False
    costs = project.get("evaluation_cloud_tasks", project.get("cloud_budget", {}).get("tasks", []))
    costs = {(value.get("shot_id"),value.get("take")):value for value in costs}
    for shot in project.get("shots", []):
        attempts = shot.get("attempts")
        if not isinstance(attempts,list):
            attempts = []
        for attempt in attempts[:100]:
            cost = costs.get((shot["id"],attempt.get("take")), {}) if attempt.get("stage") == "video" else {}
            values.append({"kind":"generation", "project_id":project["id"], "shot_id":shot["id"],
                "take":attempt.get("take"), "attempt":attempt.get("attempt"), "stage":attempt.get("stage"),
                "model":attempt.get("model"), "provider":attempt.get("provider"), "parameters":attempt.get("parameters"),
                "config_signature":attempt.get("production_profile_signature"), "source_signature":attempt.get("source_signature"),
                "elapsed_seconds":finite(attempt.get("elapsed_seconds")), "started_at":attempt.get("started_at"),
                "status":attempt.get("state"), "estimated_cost_micros":cost.get("estimated_cost_micros"),
                "confirmed_cost_micros":cost.get("confirmed_cost_micros"), "currency":cost.get("currency"),
                "quality":attempt.get("qa"), "task_id":cost.get("task_id")})
        truncated |= len(attempts) > 100
    slug = project.get("slug", "")
    if slug:
        base = within(Path(root) / "production", slug)
        paths = sorted(base.glob("**/calls/*/receipt.json")) if base.is_dir() else []
        truncated |= len(paths) > 240
        for path in paths[:240]:
            try:
                path = within(base, path)
                receipt = read_json(path, maximum=512 * 1024)
                values.append({"kind":"model_call", "project_id":project["id"], "role":receipt.get("role"),
                    "model":receipt.get("response_model"), "configured_model":receipt.get("configured_model",receipt.get("model")),
                    "model_identity":receipt.get("model_identity"), "config_revision":receipt.get("config_revision"),
                    "elapsed_seconds":finite(receipt.get("elapsed_seconds")), "attempt_count":len(receipt.get("attempts", [])),
                    "status":receipt.get("status"), "receipt":path.relative_to(root).as_posix(), "receipt_sha256":sha256(path)})
            except (ValueError, OSError, TypeError):
                values.append({"kind":"unreadable_receipt", "project_id":project["id"], "path":path.name})
    return {"records":values,"truncated":truncated,"historical_unknowns_not_inferred":True}


def evaluate(project, root, *, golden=None, human=None, vbench=None, asr_model=None, transcriber=None,
             vlm_config=None, observations_dir=None):
    project = copy.deepcopy(project)
    root = Path(root).resolve(strict=True)
    if not isinstance(project, dict) or not isinstance(project.get("id"), str):
        raise ValueError("Evaluation requires a project snapshot with its exact project ID")
    golden = read_json(GOLDEN) if golden is None else copy.deepcopy(golden)
    if golden.get("schema") != "ai-studio.golden.v1" or set(golden.get("dimensions",{})) != {"causality","identity","props","dialogue","action","subtitles","delivery"}:
        raise ValueError("Golden set must define the seven versioned film dimensions")
    start = time.monotonic()
    checks, media = deterministic(project, root)
    media_signature = fingerprint({"media":media, "dialogue":[{"id":shot.get("id"),"lines":shot.get("dialogue",[])} for shot in project.get("shots",[])]})
    checks.extend(asr_checks(project, root, asr_model, transcriber))
    if vlm_config is None:
        checks.extend(row(name,"vlm_review","not_run",source="vlm") for name in ("causality","identity","props","action"))
    else:
        if observations_dir is None:
            raise ValueError("VLM evaluation requires an explicit output folder for its audit receipts")
        from .evaluation_semantic import evaluate_semantics
        checks.extend(evaluate_semantics(project,root,vlm_config,observations_dir))
    checks.extend(vbench_checks(vbench,media))
    checks.extend(human_checks(human,project,media_signature,golden["dimensions"]))
    for shot in project.get("shots",[]):
        for receipt in (shot.get("image"),shot.get("video")):
            for reference in (receipt or {}).get("asset_references", []):
                declared = project.get("series_references",{}).get(shot["id"], [])
                present = any(all(value.get(key) == reference.get(key) for key in ("asset_id","version","sha256")) for value in declared)
                checks.append(row("identity","pinned_reference_receipt","pass" if present else "fail",shot_id=shot["id"],
                                  evidence=[reference],source="reference_metadata_not_visual_identity"))
    measured = [check for check in checks if check["source"] == "deterministic"]
    technical_passed = bool(measured) and all(check["status"] == "pass" for check in measured)
    human_rows = [check for check in checks if check["check"] == "producer_review"]
    human_passed = human is not None and human.get("decision") == "approve" and all(value["status"] == "pass" for value in human_rows)
    failures = [value for value in checks if value["status"] == "fail"]
    verdict = "needs_repair" if failures else "human_approved" if technical_passed and human_passed else "human_review_required"
    code_files = [Path(__file__),ROOT/"factory/evaluation_media.py",ROOT/"factory/evaluation_sampling.py",ROOT/"factory/evaluation_external.py",ROOT/"factory/evaluation_semantic.py",ROOT/"factory/llm.py",ROOT/"audio/check_dialogue.py"]
    return {"schema":"ai-studio.film-evaluation.v1", "run_id":uuid.uuid4().hex, "created_at":datetime.now(timezone.utc).isoformat(),
            "project_id":project["id"], "production_revision":project.get("production_revision",1), "project_revision":project.get("revision"),
            "production_profile":project.get("production_profile"), "profile_signature":fingerprint(project.get("production_profile")),
            "protocol_version":golden["version"], "golden_signature":fingerprint(golden), "dimensions":golden["dimensions"],
            "media_signature":media_signature,"media":media,"checks":checks,"verdict":verdict,
            "technical_passed":technical_passed,"human_approved":human_passed,"artistic_quality_certified":False,
            "elapsed_seconds":round(time.monotonic()-start,3),"python_version":platform.python_version(),
            "evaluation_code":{path.relative_to(ROOT).as_posix():sha256(path) for path in code_files},
            "observations":observations(project,root),
            "repair_suggestions":[{"category":name,"recommendation":golden["dimensions"][name]["repair"]} for name in sorted({item["category"] for item in checks if item["status"] in {"fail","review_needed"}})],
            "boundaries":["Automatic checks do not prove artistic merit or full temporal consistency", "ASR can misrecognize; phonetic match is not textual identity", "VBench imported scores are raw optional metrics, never a producer approval", "Old missing identities and costs remain unknown", "This evaluation never enqueues or repeats a generation POST"]}


def compare_reports(current, baseline):
    compatible = all(current.get(key) == baseline.get(key) for key in ("golden_signature","profile_signature"))
    if not compatible:
        return {"comparable":False,"reason":"Golden protocol or production profile differs"}
    def index(report):
        return {(row["category"],row["check"],row.get("shot_id"),row["source"]):row for row in report.get("checks",[])}
    previous = index(baseline)
    changes = []
    for key, value in index(current).items():
        old = previous.get(key)
        if old and old["status"] != value["status"]:
            changes.append({"category":key[0],"check":key[1],"shot_id":key[2],"source":key[3],"before":old["status"],"after":value["status"]})
    return {"comparable":True,"baseline_run_id":baseline.get("run_id"),"changes":changes,
            "note":"Protocol-aligned status comparison, not a causal attribution of artistic improvement"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--project-json",type=Path)
    source.add_argument("--project-id")
    parser.add_argument("--database",type=Path,help="Existing SQLite database; opened read-only, never initialized")
    parser.add_argument("--root",type=Path,default=ROOT,help="Root containing the recorded production paths")
    parser.add_argument("--output",type=Path,required=True,help="New report directory outside production media")
    parser.add_argument("--human-review",type=Path)
    parser.add_argument("--vbench-report",type=Path)
    parser.add_argument("--asr-model",type=Path,help="Explicit existing faster-whisper local model directory")
    parser.add_argument("--vlm-config",type=Path,help="Explicit evaluation model configuration; never used by default")
    parser.add_argument("--confirm-vlm-requests",action="store_true",help="Acknowledge optional configured model requests, which may have cost")
    parser.add_argument("--baseline",type=Path)
    args = parser.parse_args()
    if args.project_id and args.database is None:
        parser.error("--project-id requires --database")
    if args.vlm_config and not args.confirm_vlm_requests:
        parser.error("--vlm-config requires --confirm-vlm-requests; no implicit model calls")
    output, root = args.output.resolve(), args.root.resolve(strict=True)
    if output.exists() or output.is_relative_to(root / "production"):
        parser.error("output must be a new directory outside production; existing reports/media are never overwritten")
    project = read_json(args.project_json) if args.project_json else database_project(args.database,args.project_id)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eval-observations-",dir=output.parent) as audit:
        report = evaluate(project,root,human=read_json(args.human_review) if args.human_review else None,
            vbench=read_json(args.vbench_report) if args.vbench_report else None, asr_model=args.asr_model,
            vlm_config=read_json(args.vlm_config) if args.vlm_config else None,observations_dir=audit)
        if args.baseline:
            report["comparison"] = compare_reports(report,read_json(args.baseline))
        export_report(report,output,observations_dir=audit)
    print(json.dumps({"output":str(output),"project_id":project["id"],"verdict":report["verdict"],"technical_passed":report["technical_passed"],"human_approved":report["human_approved"]},ensure_ascii=False))
    return 2 if report["verdict"] == "needs_repair" else 0


if __name__ == "__main__":
    raise SystemExit(main())

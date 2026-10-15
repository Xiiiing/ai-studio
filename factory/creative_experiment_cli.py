"""Offline creative experiment CLI. No model-running command is registered."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from .creative_evidence_io import read_json, new_directory, write_new
from .creative_experiment import freeze, verify, ROOT
from .creative_comparison import ingest_generation, blind, ingest_review, compare
from .prompt_registry import load_prompt


def parser():
    root = argparse.ArgumentParser(description="Freeze/import/blind/compare creative evidence; never execute a model")
    commands = root.add_subparsers(dest="command", required=True)
    freeze_parser = commands.add_parser("freeze")
    freeze_parser.add_argument("--corpus", type=Path, required=True)
    freeze_parser.add_argument("--config", type=Path, required=True, help="Explicit config, read only; credentials are not copied")
    freeze_parser.add_argument("--role", choices=("screenwriter", "storyboard_director"), required=True)
    for arm in ("baseline", "candidate"):
        freeze_parser.add_argument("--" + arm + "-root", type=Path)
        freeze_parser.add_argument("--" + arm + "-version")
    freeze_parser.add_argument("--samples", type=int, default=3)
    freeze_parser.add_argument("--seed", type=int, default=731)
    freeze_parser.add_argument("--split", choices=("development", "holdout"), default="development")
    freeze_parser.add_argument("--allow-holdout", action="store_true")
    freeze_parser.add_argument("--judge-id", required=True)
    freeze_parser.add_argument("--judge-config", type=Path, help="Freeze a model judge's explicit identity; omit for a human judge")
    freeze_parser.add_argument("--output", type=Path, required=True)
    freeze_parser.add_argument("--seal", type=Path, required=True)
    for name in ("verify", "import-generation", "blind", "import-review", "compare"):
        command = commands.add_parser(name)
        command.add_argument("run", type=Path)
        command.add_argument("--seal", type=Path, required=True)
        if name == "import-generation":
            command.add_argument("--task", required=True)
            command.add_argument("--result", type=Path, help="Not needed for a recorded failed call")
            command.add_argument("--receipt", type=Path, required=True)
            command.add_argument("--evidence-mode", choices=("simulation", "external"), required=True)
        elif name == "import-review":
            command.add_argument("--review", type=Path, required=True)
            command.add_argument("--receipt", type=Path, help="Mandatory for externally reported model judgments")
        elif name in {"blind", "compare"}:
            command.add_argument("--output", type=Path, required=True)
    return root


def _commit():
    try:
        value = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        return value
    except (OSError, subprocess.SubprocessError):
        return None  # Unknown is not reconstructed from a release label.


def export_summary(report, destination):
    output = new_directory(destination)
    write_new(output / "report.json", report)
    lines = ["# Creative comparison", "", "Status: " + report["status"],
             "Conclusion: " + report["conclusion"],
             "Model validation: " + report["model_validation"],
             "Production approval: false", "", "Missing generations: " + str(len(report["missing_generations"])),
             "Failed generations: " + str(len(report["failed_generations"])),
             "Missing reviews: " + str(len(report["missing_reviews"]))]
    if report["summary"] is not None:
        lines.extend(["", "Observed score delta (0..10 scale, not a percentage): " +
                      str(report["summary"]["observed_delta"]),
                      "Regressed cases: " + ", ".join(report["summary"]["regressed_cases"])])
    lines.extend(["", *report["limitations"]])
    # The summary is also represented in JSON, so consumers need not parse Markdown.
    write_new(output / "summary.json", {"lines": lines})
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return output


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "freeze":
            plan = freeze(args.corpus,
                          load_prompt(args.role, args.baseline_version, root=args.baseline_root),
                          load_prompt(args.role, args.candidate_version, root=args.candidate_root),
                          read_json(args.config), args.output, args.seal, role=args.role,
                          samples=args.samples, seed=args.seed, split=args.split,
                          allow_holdout=args.allow_holdout, judge_id=args.judge_id,
                          judge_config=read_json(args.judge_config) if args.judge_config else None, git_commit=_commit())
            result = {"status": "frozen", "experiment_id": plan["experiment_id"],
                      "tasks": len(plan["tasks"]), "generation_started": False}
        elif args.command == "verify":
            plan = verify(args.run, args.seal)
            result = {"status": "verified", "experiment_id": plan["experiment_id"], "generation_started": False}
        elif args.command == "import-generation":
            result = ingest_generation(args.run, args.seal, args.task, args.result, args.receipt,
                                       evidence_mode=args.evidence_mode)
        elif args.command == "blind":
            result = blind(args.run, args.seal, args.output)
        elif args.command == "import-review":
            result = ingest_review(args.run, args.seal, args.review, receipt_path=args.receipt)
        else:
            result = compare(args.run, args.seal)
            export_summary(result, args.output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 2 if result.get("status") in {"not_run", "incomplete"} else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "generation_started": False}, ensure_ascii=False))
        return 2

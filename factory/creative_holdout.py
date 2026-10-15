"""Reserve held-out cases before exporting inputs, not after inspecting scores.

The operator owns the common seal directory. Renaming cases or discarding that
trusted directory cannot make previously exposed material unseen. Reservations
are immutable and retained after interruption; no paid execution is performed.
"""
from pathlib import Path

from .creative_evidence_io import digest, read_json, write_new


def reserve_holdout(plan, seal_path, *, verify_only=False):
    if plan["split"] != "holdout":
        return
    seal = Path(seal_path).absolute()
    expected = {"protocol": plan["protocol"], "plan_sha256": digest(plan),
                "seal_name": seal.name, "status": "released"}
    paths = [seal.parent / (".creative-holdout-release-" + digest(case["id"]) + ".json")
             for case in plan["cases"]]
    message = "holdout already consumed/released to another plan or seal; retire it to development"
    for path in paths:
        if path.exists() and read_json(path) != expected:
            raise ValueError(message)
        if verify_only and not path.exists():
            raise ValueError("Missing trusted holdout release reservation")
    if verify_only:
        return
    # O_EXCL makes each claim race-safe. Partial reservations are conservative:
    # a failed batch never exports inputs, and only the exact same plan/seal can
    # reconcile an interrupted reservation. No rollback erases exposure history.
    for path in paths:
        try:
            write_new(path, expected)
        except FileExistsError:
            if read_json(path) != expected:
                raise ValueError(message)

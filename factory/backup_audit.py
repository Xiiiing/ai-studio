"""Raw relational/reference audits; never deserialize or execute checkpoint payloads."""
from __future__ import annotations

from contextlib import ExitStack, closing
from pathlib import Path
import re

from . import backup_files as files
from . import backup_sqlite as sql
from .backup_files import BackupError


LIMIT = 100000


def rows(db, query):
    values = db.execute(query).fetchmany(LIMIT + 1)
    if len(values) > LIMIT: raise BackupError("Database audit exceeds bounded row count")
    return values


def receipt_references(value, catalog, *, base=None):
    pending, checked = [value], 0
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if "path" in node and "sha256" in node:
                name = files.relative(node["path"])
                if not name.startswith("production/"):
                    if base is None: raise BackupError("Non-portable or unbacked recorded media path: " + name)
                    name = (Path(base) / name).as_posix()
                if name not in catalog: raise BackupError("Referenced production material missing: " + name)
                receipt = catalog[name]
                if node["sha256"] != receipt["sha256"] or node.get("bytes", receipt["bytes"]) != receipt["bytes"]:
                    raise BackupError("Referenced media checksum differs: " + name)
                checked += 1
            pending.extend(node.values())
        elif isinstance(node, list): pending.extend(node)
    return checked


def inspect(root, readers, media):
    catalog = {item["path"]: item for item in media}
    schemas = {name: sql.schema(db, name) for name, db in readers.items()}
    db, checkpoints = readers["studio.sqlite3"], readers["checkpoints.sqlite3"]
    projects, identities, references = [], set(), 0
    hierarchy = {}
    for row in rows(db, "SELECT id,revision,snapshot FROM projects ORDER BY id"):
        project = files.decode_json(row["snapshot"])
        pid = row["id"]
        if (not isinstance(project, dict) or not isinstance(pid, str) or not re.fullmatch(r"[a-f0-9]{12}", pid) or
            project.get("id") != pid or project.get("revision", row["revision"]) != row["revision"]):
            raise BackupError("Project snapshot and SQLite revision/identity differ")
        slug = project.get("slug", "factory-" + pid)
        if not isinstance(slug, str) or not re.fullmatch(r"factory-[A-Za-z0-9_-]{1,60}", slug):
            raise BackupError("Non-portable project slug")
        owner = f"production/{slug}/factory_owner.json"
        if owner in catalog and files.read_json(root, owner).get("project_id") != pid:
            raise BackupError("Project production directory owner differs")
        identities.add(pid)
        hierarchy[pid]=project
        references += receipt_references(project, catalog)
        projects.append({"id": pid, "slug": slug, "revision": row["revision"],
                         "production_revision": project.get("production_revision"), "status": project.get("status"),
                         "snapshot_sha256": files.digest(project)})
    for pid,project in hierarchy.items():
        kind=project.get('record_kind')
        if kind=='drama':
            for eid in project.get('episodes',[]):
                if hierarchy.get(eid,{}).get('drama_id')!=pid: raise BackupError('Orphaned drama episode relationship')
            for batch in project.get('batches',[]):
                if not set(batch['episode_ids'])<=set(project['episodes']): raise BackupError('Foreign episode in drama batch')
        elif kind in {'episode','scene_unit'}:
            root=hierarchy.get(project.get('drama_id'),{})
            if root.get('record_kind')!='drama' or not 1<=project.get('plan_version',0)<=root.get('plan_version',0):
                raise BackupError('Invalid pinned drama version or parent')
            if kind=='episode' and pid not in root.get('episodes',[]): raise BackupError('Orphaned drama episode relationship')
            if kind=='scene_unit' and pid not in hierarchy.get(project.get('episode_id'),{}).get('unit_ids',[]):
                raise BackupError('Orphaned scene unit relationship')
    threads = {r[0] for r in rows(checkpoints, "SELECT thread_id FROM checkpoints UNION SELECT thread_id FROM writes")}
    if not threads <= identities: raise BackupError("Checkpoint database contains another project/thread")
    for table in ("memories", "idempotency"):
        if any(row[0] not in identities for row in rows(db, "SELECT DISTINCT project_id FROM " + table)):
            raise BackupError("Orphaned project relationship in " + table)
    for row in rows(db, "SELECT document,sha256 FROM series_bibles"):
        if files.digest(files.decode_json(row["document"])) != row["sha256"]:
            raise BackupError("Series Bible version checksum differs")
    assets = {}
    for row in rows(db, "SELECT asset_id,version,status,data FROM series_assets"):
        data = files.decode_json(row["data"])
        if not isinstance(data, dict): raise BackupError("Series asset version data is not an object")
        references += receipt_references(data, catalog)
        assets[(row["asset_id"], row["version"])] = (row["status"], data)
    for row in rows(db, "SELECT asset_id,version,sha256 FROM series_dependencies"):
        state, asset = assets[(row["asset_id"], row["version"])]
        if state != "approved" or asset.get("receipt", {}).get("sha256") != row["sha256"]:
            raise BackupError("Pinned series dependency differs from its approved version")
    for row in rows(db, "SELECT scope FROM cloud_batches"):
        references += receipt_references(files.decode_json(row[0]), catalog)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_versions'").fetchone():
        materials = {}
        for row in rows(db, "SELECT asset_id,version,status,data FROM material_versions"):
            data = files.decode_json(row["data"])
            references += receipt_references(data, catalog)
            materials[(row["asset_id"], row["version"])] = (row["status"], data)
        for row in rows(db, "SELECT snapshot FROM projects"):
            project = files.decode_json(row[0])
            package = project.get("material_package", {})
            if package and files.digest(package["references"]) != package.get("signature"):
                raise BackupError("Material package signature differs from its fixed references")
            for ref in package.get("references", []):
                status, data = materials.get((ref["asset_id"], ref["version"]), (None, {}))
                if status != "approved" or files.digest({"document": data.get("document"), "images": data.get("images")}) != ref["sha256"]:
                    raise BackupError("Pinned material metadata differs from its approved version")
        for row in rows(db, "SELECT d.asset_id,d.version,v.status FROM material_dependencies d LEFT JOIN material_versions v ON v.asset_id=d.asset_id AND v.version=d.version"):
            if row["status"] != "approved":
                raise BackupError("Pinned material dependency is missing or unapproved")
    # Include receipt/manifest references in historical takes and deliveries, not
    # merely the currently selected media. Do not follow URLs or execute scripts.
    for name in catalog:
        if name.endswith(".json"):
            references += receipt_references(files.read_json(root, name), catalog, base=str(Path(name).parent))
    tasks = [dict(row) for row in rows(db, "SELECT intent_id,project_id,shot_id,task_id,file_id,status,request_signature,currency FROM cloud_tasks WHERE status NOT IN ('downloaded','failed','cancelled') ORDER BY intent_id")]
    material_jobs = 0
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_tasks'").fetchone():
        for row in rows(db, "SELECT data,result FROM material_tasks"):
            references += receipt_references(files.decode_json(row["data"]), catalog)
            references += receipt_references(files.decode_json(row["result"]), catalog)
        for row in rows(db, "SELECT intent_id,task_id,status,request_signature,currency,receipt FROM material_submissions"):
            if row["receipt"]:
                references += receipt_references(files.decode_json(row["receipt"]), catalog)
            if row["status"] not in {"downloaded", "failed", "rejected"}:
                tasks.append({"intent_id": row["intent_id"], "material_task_id": row["task_id"], "status": row["status"], "request_signature": row["request_signature"], "currency": row["currency"]})
        material_jobs = db.execute("SELECT count(*) FROM material_tasks WHERE status IN ('queued','running','pause_requested')").fetchone()[0]
    return {"projects": projects, "schemas": schemas, "checkpoint_threads": sorted(threads),
            "unresolved_cloud_tasks": tasks, "checked_references": references,
            "queued_or_running_jobs": db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] + material_jobs,
            "checkpoint_payloads": "opaque_bytes_preserved_never_deserialized",
            "cross_database_audit": "thread/project, relational foreign keys and fixed asset references; not semantic replay"}


def inspect_copies(root, media):
    with ExitStack() as stack:
        readers = {name: stack.enter_context(closing(sql.connect(Path(root) / sql.DATA_PATH / name))) for name in sql.NAMES}
        return inspect(root, readers, media)

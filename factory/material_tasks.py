"""Durable library-owned AI jobs consumed by the existing singleton worker.

No synthetic project is created. Image submissions have their own owner in the
same SQLite ledger and use the existing model transports and pricing contract.
"""
from __future__ import annotations

import base64
import copy
import json
import time
import uuid

from studio import core
from . import store, material_library as library
from .config import load_config, resolve_llm, llm_identity
from .media_profiles import resolve, identity
from .cloud_budget import pricing
from .scheduler import ProductionNeedsDecision, ProductionPaused

OPERATIONS = {"describe", "generate", "restyle", "views", "compose", "review"}


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS material_tasks(
      id TEXT PRIMARY KEY, library_key TEXT NOT NULL, signature TEXT NOT NULL,
      request_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, lease INTEGER NOT NULL DEFAULT 0,
      data TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', error TEXT,
      created REAL NOT NULL, updated REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS material_tasks_queue ON material_tasks(status,created);
    CREATE TABLE IF NOT EXISTS material_submissions(
      intent_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES material_tasks(id),
      request_signature TEXT NOT NULL, status TEXT NOT NULL, response TEXT,
      receipt TEXT, estimated_cost_micros INTEGER, currency TEXT NOT NULL,
      created REAL NOT NULL, updated REAL NOT NULL);
    """)


def read(db, tid):
    library.identifier(tid)
    row = db.execute("SELECT * FROM material_tasks WHERE id=?", (tid,)).fetchone()
    if not row:
        raise KeyError("素材任务不存在")
    return {**dict(row), "data": json.loads(row["data"]), "result": json.loads(row["result"])}


def public(task):
    data = task["data"]
    return {**{key: task[key] for key in ("id", "library_key", "status", "lease", "result", "error", "created", "updated")},
            "operation": data["operation"], "name": data["document"]["name"], "quote": data.get("quote"),
            "authorization": data.get("authorization"), "source": data.get("source"),
            "style": {k: data["style"][k] for k in ("asset_id", "version")} if data.get("style") else None,
            "model": data.get("image_profile", {}).get("model") or data.get("vision_identity", {}).get("model"),
            "signature": task["signature"], "model_capabilities": data.get("model_capability_snapshot")}


def tasks(project_id=None):
    with store.connection() as db:
        project = store._read(db, project_id) if project_id else None
        keys = ["global"] + ([library.library_key(project)] if project else [])
        rows = db.execute("SELECT id FROM material_tasks WHERE library_key IN (" + ",".join("?" for _ in keys) + ") ORDER BY created DESC LIMIT 100", keys).fetchall()
        return {"tasks": [public(read(db, row[0])) for row in rows]}


def reference_limit(profile):
    from .model_capabilities import describe
    capability = describe(profile, 'image')
    if capability['status'] == 'unknown' or not capability['reference_counts']:
        raise ValueError('图像模型参考能力尚未明确，请检查工作流与连接')
    return max(capability['reference_counts'])

def _steps(count, maximum):
    if count <= maximum:
        return 1
    if maximum < 2:
        raise ValueError("该工作流无法容纳组合参考，请配置支持多图的工作流")
    return 1 + (count - maximum + maximum - 2) // (maximum - 1)


def create(payload, config=None):
    allowed = {"request_id", "project_id", "scope", "operation", "asset_id", "version", "kind", "document", "style", "views", "prompt"}
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get("operation") not in OPERATIONS:
        raise ValueError("素材AI任务字段无效")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
        raise ValueError("素材任务需要稳定的request_id")
    config = copy.deepcopy(config if config is not None else load_config())
    operation = payload["operation"]
    views = payload.get("views", ["主参考"])
    if not isinstance(views, list) or not 1 <= len(views) <= 4 or any(not isinstance(v, str) or not 1 <= len(v) <= 100 for v in views) or len(set(views)) != len(views):
        raise ValueError("请指定1–4个不同的参考视图")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 4000:
        raise ValueError("生成要求不能超过4000字")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, payload["project_id"]) if payload.get("project_id") else None
        source = library.accessible(library.get(db, payload["asset_id"], payload.get("version")), project) if payload.get("asset_id") else None
        kind = source["kind"] if source else payload.get("kind")
        if kind not in library.KINDS:
            raise ValueError("素材类型无效")
        document = library.validate_document(payload.get("document", source["data"]["document"] if source else None), kind)
        library._validate_links(db, document, project)
        key = library.library_key(project) if project and payload.get("scope", "project") != "global" else "global"
        if key == "global":
            library._validate_links(db, document, None)
            if source and source["library_key"] != "global":
                raise ValueError("请先明确保存到统一库，再创建统一库AI任务")
        data = {"operation": operation, "kind": kind, "document": document, "prompt": prompt, "views": views,
                "project_id": project["id"] if project else None, "config": config, "inputs": []}
        if source:
            data["source"] = {"asset_id": source["asset_id"], "version": source["version"]}
            if source["data"]["images"]:
                data["inputs"].append({"purpose": "identity" if kind == "character" else "source", "receipt": source["data"]["images"][0]})
        if operation in {"describe", "review"}:
            if not source or not source["data"]["images"]:
                raise ValueError("识图或审核需要已有参考图片")
            resolve_llm(config, "visual_critic", require_vision=True)
            data["vision_identity"] = llm_identity(config, "visual_critic")
            data["inputs"] = [{"purpose": "source", "receipt": image} for image in source["data"]["images"]]
        else:
            profile = resolve(config, "image")
            if not profile.get("enabled", True):
                raise ValueError("请先启用图像连接")
            data["image_profile"] = profile
            from .model_capabilities import describe
            data["model_capability_snapshot"] = describe(profile, "image")
            # Generation candidates always receive an independent visual review.
            resolve_llm(config, "visual_critic", require_vision=True)
            data["vision_identity"] = llm_identity(config, "visual_critic")
            if operation in {"restyle", "views"} and not data["inputs"]:
                raise ValueError("转换画风和补视图需要原始参考图片")
            if operation == "compose":
                if kind != "composition" or not document.get("members"):
                    raise ValueError("请先指定组合中的人物及对应角色")
                data["inputs"] = []
                for member in document["members"] + ([document["scene"]] if document.get("scene") else []):
                    value = library.accessible(library.get(db, member["asset_id"], member["version"]), project)
                    if not value["data"]["images"]:
                        raise ValueError("组合中的人物和场景需要参考图片")
                    data["inputs"].append({"purpose": member.get("role", "scene"), "receipt": value["data"]["images"][0]})
            if payload.get("style"):
                if not isinstance(payload["style"], dict) or set(payload["style"]) != {"asset_id", "version"}:
                    raise ValueError("画风需要固定素材编号和版本")
                style = library.accessible(library.get(db, payload["style"].get("asset_id"), payload["style"].get("version")), project)
                if key == "global" and style["library_key"] != "global":
                    raise ValueError("统一库任务不能隐式引用剧内私有画风")
                if style["kind"] != "style":
                    raise ValueError("请选择画风素材")
                data["style"] = {"asset_id": style["asset_id"], "version": style["version"], "document": style["data"]["document"]}
                if style["data"]["images"]:
                    data["inputs"].append({"purpose": "style_only", "receipt": style["data"]["images"][0]})
            elif operation == "restyle":
                raise ValueError("转换画风需要指定项目画风")
            maximum = reference_limit(profile)
            data["reference_limit"] = maximum
            estimate = pricing(profile.get("pricing"))
            count = _steps(len(data["inputs"]), maximum) * len(views)
            data["quote"] = {**estimate, "submissions": count,
                             "estimated_total_micros": None if estimate["unit_cost_micros"] is None else count * estimate["unit_cost_micros"],
                             "paid": profile["provider"] == "dashscope_image"}
        for item in data["inputs"]:
            library.image_path(item["receipt"])
        signature = core.fingerprint({"library_key": key, **{k: v for k, v in data.items() if k != "config"}})
        previous = db.execute("SELECT id,signature FROM material_tasks WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            if previous["signature"] != signature:
                raise ValueError("request_id已用于其他素材任务")
            return public(read(db, previous["id"]))
        previous = db.execute("SELECT id FROM material_tasks WHERE signature=? AND library_key=? AND status IN ('queued','running','waiting_authorization','completed') ORDER BY created DESC LIMIT 1", (signature, key)).fetchone()
        if previous:
            return public(read(db, previous[0]))
        tid = "mt_" + uuid.uuid4().hex
        state = "waiting_authorization" if data.get("quote", {}).get("paid") else "queued"
        db.execute("INSERT INTO material_tasks(id,library_key,signature,request_id,status,data,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                   (tid, key, signature, request_id, state, store.encoded(data), time.time(), time.time()))
        return public(read(db, tid))


def control(tid, payload):
    if not isinstance(payload, dict) or set(payload) - {"action", "expected_lease", "signature", "confirmed", "allow_unpriced", "max_cost_micros", "project_id"}:
        raise ValueError("素材任务控制字段无效")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        task = read(db, tid)
        project = store._read(db, payload["project_id"]) if payload.get("project_id") else None
        if task["library_key"] != "global" and (not project or task["library_key"] != library.library_key(project)):
            raise ValueError("不能操作其他剧的素材任务")
        if payload.get("expected_lease") != task["lease"] or payload.get("signature") != task["signature"]:
            raise ValueError("任务已更新，请刷新后操作")
        action, data = payload.get("action"), task["data"]
        if action == "authorize":
            if task["status"] != "waiting_authorization" or payload.get("confirmed") is not True:
                raise ValueError("请明确授权当前素材生成批次")
            quote = data["quote"]
            amount = quote["estimated_total_micros"]
            limit = payload.get("max_cost_micros", amount)
            if amount is None:
                if payload.get("allow_unpriced") is not True:
                    raise ValueError("供应商价格未知，需要明确接受未知价格")
            elif type(limit) is not int or limit < amount or limit > 10**12:
                raise ValueError("授权金额不足或无效")
            data["authorization"] = {"signature": task["signature"], "max_submissions": quote["submissions"],
                                     "max_cost_micros": limit, "allow_unpriced": amount is None, "at": time.time(), "actor": "producer"}
            state = "queued"
        elif action == "pause":
            state = "pause_requested" if task["status"] == "running" else "paused" if task["status"] == "queued" else task["status"]
        elif action == "resume":
            if task["status"] not in {"paused", "needs_attention", "failed"}:
                raise ValueError("任务当前不能恢复")
            state = "queued"
        else:
            raise ValueError("不支持的素材任务操作")
        db.execute("UPDATE material_tasks SET status=?,data=?,error=NULL,updated=? WHERE id=?", (state, store.encoded(data), time.time(), tid))
        return public(read(db, tid))


def recover():
    with store.connection() as db:
        db.execute("UPDATE material_tasks SET status=CASE WHEN status='pause_requested' THEN 'paused' ELSE 'queued' END,lease=lease+1,updated=? WHERE status IN ('running','pause_requested')", (time.time(),))


def claim():
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id FROM material_tasks WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
        if not row:
            return None
        db.execute("UPDATE material_tasks SET status='running',lease=lease+1,updated=? WHERE id=?", (time.time(), row[0]))
        return read(db, row[0])


def boundary(task):
    with store.connection() as db:
        current = read(db, task["id"])
        if current["lease"] != task["lease"] or current["status"] != "running":
            raise ProductionPaused("素材任务已暂停或执行权已更新")


def _save_result(task, result, status="running", error=None):
    with store.connection() as db:
        return db.execute("UPDATE material_tasks SET status=CASE WHEN status='pause_requested' AND ?='running' THEN status ELSE ? END,result=?,error=?,updated=? WHERE id=? AND lease=? AND status IN ('running','pause_requested')",
                          (status, status, store.encoded(result), error, time.time(), task["id"], task["lease"])).rowcount == 1


def _folder(task):
    return core.within(core.ROOT / "production/material-tasks", task["id"])


def vision(task, images, *, review=False):
    from .llm import _request, _image_url
    data = task["data"]
    fields = {name: {"type": "string", "maxLength": 4000} for name in ("name", "description", "identity_constraints", "default_wardrobe", "visual_rules")}
    fields["name"]["minLength"] = 1
    document_schema = {"type": "object", "additionalProperties": False, "properties": fields, "required": list(fields)}
    checks = {name: {"type": "string", "enum": ["match", "mismatch", "uncertain", "not_applicable"]} for name in ("identity", "style", "scene", "correspondence")}
    schema = ({"type": "object", "additionalProperties": False, "properties": {"status": {"type": "string", "enum": ["pass", "fail", "uncertain"]}, "summary": {"type": "string"}, **checks}, "required": ["status", "summary", *checks]} if review else document_schema)
    system = ("你是独立素材视觉审核。逐项检查人物身份、指定画风、场景结构及多人对应关系；技术检查不能证明这些。前面的图片是有用途标签的来源，最后是候选。仅以可见证据判定，无法判断填uncertain；不要把参考的画风差异直接认定为身份漂移。" if review else
              "你是素材建档助手。仅描述图片中可见的人物外观、场景与画风，身份名称沿用用户输入；无法确定的属性写未知，不推断真实身份。输出可供用户编辑的草稿。")
    content = [{"type": "text", "text": json.dumps({"kind": data["kind"], "document": data["document"], "style": data.get("style"), "prompt": data["prompt"]}, ensure_ascii=False)}]
    for label, path in images:
        content += [{"type": "text", "text": label}, {"type": "image_url", "image_url": {"url": _image_url(path)}}]
    result = _request(data["config"], "visual_critic", system, None, schema, _folder(task) / "vision", max_tokens=1800, temperature=.15, content=content)
    if review:
        if "mismatch" in result.values(): result["status"] = "fail"
        elif "uncertain" in result.values(): result["status"] = "uncertain"
        result.update(scope="vlm_material_review_not_human_certification", model=data["vision_identity"])
    return result


def _cloud_image(task, prompt, refs, folder, step):
    from .cloud_media import image_body, _request
    from .video_transport import download_video
    from .cloud_budget import validate_live_profile
    data, profile = task["data"], task["data"]["image_profile"]
    body = image_body(profile, prompt, refs, (768, 1024), int(task["signature"][:8], 16))
    signature = core.fingerprint({"body": body, "connection": identity(profile)})
    intent = core.fingerprint({"task": task["id"], "step": step})
    with store.connection() as db:
        row = db.execute("SELECT * FROM material_submissions WHERE intent_id=?", (intent,)).fetchone()
    if row:
        if row["request_signature"] != signature or not row["response"]:
            raise ProductionNeedsDecision("素材云端提交结果不确定；请核对供应商记录，禁止自动重投", "material_submission_unknown")
        if row["status"] in {"failed", "rejected"}:
            raise ProductionNeedsDecision("此素材请求已失败，保留原回执；修改要求后新建任务", "material_submission_failed")
        response = json.loads(row["response"])
    else:
        boundary(task)
        authorization = data.get("authorization", {})
        if authorization.get("signature") != task["signature"]:
            raise ProductionNeedsDecision("此素材批次尚未授权", "material_authorization_required")
        validate_live_profile({**profile, "_media_kind": "image"})
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            live = read(db, task["id"])
            if live["lease"] != task["lease"] or live["status"] != "running":
                raise ProductionPaused()
            used = db.execute("SELECT count(*) FROM material_submissions WHERE task_id=?", (task["id"],)).fetchone()[0]
            if used >= authorization["max_submissions"]:
                raise ProductionNeedsDecision("素材批次次数已用尽", "material_budget_exhausted")
            unit = data["quote"]["unit_cost_micros"]
            if unit is not None and (used + 1) * unit > authorization["max_cost_micros"]:
                raise ProductionNeedsDecision("素材批次金额已用尽", "material_budget_exhausted")
            db.execute("INSERT INTO material_submissions VALUES(?,?,?,?,NULL,NULL,?,?,?,?)", (intent, task["id"], signature, "dispatching", unit, data["quote"]["currency"], time.time(), time.time()))
        response = _request(profile, body)  # Intent persisted before the only paid POST.
        with store.connection() as db:
            db.execute("UPDATE material_submissions SET response=?,status='generated',updated=? WHERE intent_id=?", (store.encoded(response), time.time(), intent))
    if response.get("code") or response.get("error") or response.get("model", profile["model"]) != profile["model"]:
        raise ProductionNeedsDecision("供应商生成失败或返回模型不匹配", "material_generation_failed")
    outputs = response.get("data", [])
    if len(outputs) != 1 or not isinstance(outputs[0].get("url"), str):
        raise ProductionNeedsDecision("素材响应缺少唯一下载地址", "material_download_missing")
    path = folder / "image.png"
    manifest = folder / "result.json"
    if manifest.exists():
        receipt = core.read_json(manifest)
        if core.sha256(path) != receipt["sha256"]:
            raise ValueError("生成图片缓存校验失败")
    else:
        download_video(profile, outputs[0]["url"], path)
        receipt = core.media_receipt(path, provider=profile["provider"], model=profile["model"], request_id=response.get("id", response.get("request_id")), usage=response.get("usage", {}), intent_id=intent)
        core.write_json(manifest, receipt)
    with store.connection() as db:
        db.execute("UPDATE material_submissions SET status='downloaded',receipt=?,updated=? WHERE intent_id=?", (store.encoded(receipt), time.time(), intent))
    return path, receipt


def _render_image(task, prompt, refs, folder, step, budget):
    from .scheduler import Budget, Scheduler
    from .image_generation import image_request
    data, profile = task["data"], task["data"]["image_profile"]
    if data.get('model_capability_snapshot'):
        from .model_capabilities import describe, validate, CapabilityError
        capability = data['model_capability_snapshot']
        if capability != describe(profile, 'image'):
            raise CapabilityError('素材任务的模型能力已变化，原任务与素材保留', 'snapshot')
        validate(capability, {'prompt': prompt, 'references': refs, 'width': 768, 'height': 1024, 'inspect_files': True})
    folder.mkdir(parents=True, exist_ok=True)
    if profile["provider"] == "dashscope_image":
        return _cloud_image(task, prompt, refs, folder, step)
    config = {**data["config"].get("production", {}), "gpu_service": data["config"].get("gpu_service")} if data["config"].get("gpu_service") else data["config"].get("production", {})
    if profile['provider']=='comfyui_qwen_image21' and not config.get('gpu_service'):
        raise ValueError('Qwen-Image-2.1 需要配置 Wkp 持久任务服务')
    seed = int(task["signature"][:8], 16)
    if profile["provider"] == "comfyui_external":
        from .external_comfy import ExternalComfy
        client = ExternalComfy(profile)
        names = [client.upload(path) for path in refs]
        template = profile.get("reference_templates", {}).get(str(len(names)), profile.get("workflow_template"))
        output = None
        if template:
            from .workflow_templates import load_template, bind
            loaded = load_template(template)
            graph = bind(loaded, {"prompt": prompt, "negative_prompt": "distorted faces, text, watermark", "seed": seed, "width": 768, "height": 1024}, names)
            output = loaded["output"]
        else:
            if profile.get("reference_templates"):
                raise ValueError("当前参考图数量没有对应工作流模板")
            graph = image_request(profile, prompt, seed, 768, 1024, references=names)
        for node in graph.values():
            for field, filename in profile.get("model_files", {}).items():
                if field in node["inputs"]: node["inputs"][field] = filename
        path = client.execute(graph, folder, "image", output, check=lambda: boundary(task))
    else:
        # Scheduler routes configured Wkp GPU service to its durable remote worker.
        with Scheduler(config, budget).worker(task["id"], lambda: _paused(task)) as worker:
            names = [worker.copy_input(path) for path in refs]
            template = profile.get("reference_templates", {}).get(str(len(names)), profile.get("workflow_template"))
            if template or profile.get("model_files"):
                if config.get("gpu_service"):
                    raise ValueError("专用GPU服务使用固定允许图；自定义工作流请使用外部ComfyUI连接")
                if template:
                    from .workflow_templates import load_template, bind
                    loaded = load_template(template)
                    graph = bind(loaded, {"prompt": prompt, "negative_prompt": "distorted faces, text, watermark", "seed": seed, "width": 768, "height": 1024, "frames": 1, "fps": 24}, names)
                    # Local owned worker expects a unique image output; explicit custom outputs use its HTTP adapter.
                    from .external_comfy import ExternalComfy
                    local = ExternalComfy({"base_url": worker.base, "network_mode": "loopback", "allowed_hosts": []})
                    path = local.execute(graph, folder, "image", loaded["output"], check=lambda: boundary(task))
                else:
                    graph = image_request(profile, prompt, seed, 768, 1024, references=names, prefix=task["id"])
                    for node in graph.values():
                        for field, filename in profile["model_files"].items():
                            if field in node["inputs"]: node["inputs"][field] = filename
                    path = worker.execute(graph, folder, "image", timeout=budget.timeout(1800))
            else:
                if profile.get("reference_templates"):
                    raise ValueError("当前参考图数量没有对应工作流模板")
                graph = image_request(profile, prompt, seed, 768, 1024, references=names, prefix=task["id"])
                path = worker.execute(graph, folder, "image", timeout=budget.timeout(1800))
    receipt = core.media_receipt(path, provider=profile["provider"], model=profile["model"], connection=identity(profile))
    if (folder / "result.json").exists(): receipt.update(core.read_json(folder / "result.json"))
    task["result"]["production"] = budget.snapshot()
    _save_result(task, task["result"])
    return path, receipt


def render_image(task, prompt, refs, folder, step):
    from .scheduler import Budget
    data = task["data"]
    config = copy.deepcopy(data["config"].get("production", {}))
    if data["config"].get("gpu_service"):
        config["gpu_service"] = data["config"]["gpu_service"]
    budget = Budget(config, task["result"].get("production"))
    try:
        return _render_image(task, prompt, refs, folder, step, budget)
    finally:
        task["result"]["production"] = budget.snapshot()
        _save_result(task, task["result"])


def _paused(task):
    try:
        boundary(task)
        return False
    except ProductionPaused:
        return True


def run(task):
    result = copy.deepcopy(task["result"])
    task["result"] = result
    try:
        boundary(task)
        data = task["data"]
        references = [(item["purpose"], library.image_path(item["receipt"])) for item in data["inputs"]]
        if data["operation"] == "describe":
            result["document"] = {**data["document"], **vision(task, references)}
            result["draft"] = True
        elif data["operation"] == "review":
            result["review"] = vision(task, references, review=True)
        else:
            rendered = result.setdefault("images", [])
            for view_index, view in enumerate(data["views"]):
                if view_index < len(rendered):
                    library.image_path(rendered[view_index]); continue
                boundary(task)
                prompt = ("Create a single production design reference. Preserve each supplied person's identity and the scene's spatial structure. "
                          "Style-only references guide rendering, not person identity, clothing or scene contents. Apply explicit wardrobe and state changes. "
                          + json.dumps({"document": data["document"], "view": view, "style": data.get("style"), "requirements": data["prompt"], "reference_order": [label for label, _ in references]}, ensure_ascii=False))
                remaining = list(references)
                maximum = data["reference_limit"]
                stage = 0
                prior = None
                while True:
                    batch = ([("confirmed_composite", prior)] if prior else []) + remaining[:maximum - (1 if prior else 0)]
                    remaining = remaining[len(batch) - (1 if prior else 0):]
                    folder = _folder(task) / ("view_%02d_stage_%02d" % (view_index, stage))
                    stage_prompt = (prompt + " This stage contains only the following ordered input references: " +
                                    json.dumps([label for label, _ in batch], ensure_ascii=False) +
                                    ". Depict only the identities represented in this stage; later stages add remaining subjects. " +
                                    ("Preserve all identities already present in the first composite and add the newly referenced subjects." if prior else ""))
                    core.write_json(folder / "input.json", {"prompt": stage_prompt, "reference_roles": [label for label, _ in batch],
                        "references": [core.media_receipt(path) for _, path in batch], "task_signature": task["signature"]})
                    path, receipt = render_image(task, stage_prompt, [path for _, path in batch], folder, f"{view_index}:{stage}")
                    if task["result"].get("production"):
                        result["production"] = task["result"]["production"]
                    if not remaining: break
                    prior, stage = path, stage + 1
                # Use the common upload validation and immutable publication boundary.
                uploaded = library._upload({"asset_id": task["id"], "version": view_index + 1}, base64.b64encode(path.read_bytes()).decode(), view)
                uploaded["generation"] = {key: value for key, value in receipt.items() if key not in {"path", "sha256", "bytes"}}
                rendered.append(uploaded)
                result["images"] = rendered
                _save_result(task, result)
            try:
                result["review"] = vision(task, references + [("candidate " + image["label"], library.image_path(image)) for image in rendered], review=True)
            except Exception as exc:
                result["review"] = {"status": "unavailable", "scope": "not_reviewed", "detail": type(exc).__name__}
            boundary(task)
            with store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                live = read(db, task["id"])
                if live["lease"] != task["lease"] or live["status"] != "running":
                    raise ProductionPaused()
                if not result.get("asset_id"):
                    aid = "mat_" + uuid.uuid4().hex
                    db.execute("INSERT INTO material_assets VALUES(?,?,?,0,0)", (aid, task["library_key"], data["kind"]))
                    output = {"document": data["document"], "images": rendered, "review": result["review"],
                              "task_id": task["id"], "source": data.get("source"), "style": data.get("style"), "input_signature": task["signature"]}
                    value = library._append(db, library.get_head(db, aid), output)
                    result.update(asset_id=aid, version=value["version"])
                    db.execute("UPDATE material_tasks SET result=? WHERE id=? AND lease=?", (store.encoded(result), task["id"], task["lease"]))
        _save_result(task, result, "completed")
    except ProductionPaused as exc:
        _save_result(task, result, "paused", str(exc))
    except Exception as exc:
        # Never publish raw provider exceptions or signed URLs to the UI.
        state = "needs_attention" if isinstance(exc, ProductionNeedsDecision) else "failed"
        message = str(exc) if isinstance(exc, (ProductionNeedsDecision, ValueError)) else "素材任务未完成：" + type(exc).__name__
        _save_result(task, result, state, message[:500])

#!/usr/bin/env python3
"""A bounded 512-token language/JSON probe of the running local planner."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import uuid
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from planner.client import check_model, context_budget, request_json
from planner.generate import project_path, write_json
from planner.schema import obj, text_schema, validate_schema

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT/"config/planner.json")
    parser.add_argument("--model")
    args = parser.parse_args()
    config = json.loads(project_path(args.config).read_text(encoding="utf-8"))
    if args.model:
        config["model"] = args.model
    schema = obj({"title": text_schema(2, 50), "visual": text_schema(180, 360), "emotion": text_schema(2, 40)})
    messages = [{"role": "system", "content": "只返回严格JSON。title和emotion必须使用简体中文；visual必须使用纯ASCII英文。visual用180到360个字符描述成年女店员在书店修补一本没有文字的旧书，只写一个可拍摄的动作，不生成任何可读文字。"},
                {"role": "user", "content": "这是本地接口测试。title写测试场景，emotion用中文描述平静而专注的表演，visual按规则写英文。"}]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    folder = ROOT/"planner/runs"/("probe-"+stamp)
    request = {"model": config["model"], "messages": messages, "stream": False, "n": 1,
               "temperature": 0.4, "seed": config.get("seed", 20261009), "max_tokens": 512,
               "chat_template_kwargs": config.get("chat_template_kwargs", {"enable_thinking": False}),
               "response_format": {"type": "json_object", "schema": schema}}
    receipt = {"status": "starting", "model": config["model"], "max_tokens": 512, "artifact_status": "endpoint_test_only"}
    write_json(folder/"request.json", request)
    write_json(folder/"receipt.json", receipt)
    start = time.perf_counter()
    try:
        receipt["served_model"] = check_model(config)
        receipt["context_check"] = context_budget(config, messages, 512)
        if not receipt["context_check"]["fits"]:
            raise ValueError("Probe context does not fit")
        response = request_json(config["base_url"], "/chat/completions", request, timeout=90,
                                api_key_env=config.get("api_key_env"))
        write_json(folder/"response.json", response)
        choice = response["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Probe did not end with a complete response: " + str(choice.get("finish_reason")))
        value = json.loads(choice["message"]["content"])
        errors = validate_schema(value, schema)
        if not errors:
            if not all(32 <= ord(x) <= 126 for x in value["visual"]):
                errors.append("visual is not printable ASCII English")
            if not re.search(r"[A-Za-z]{3,}", value["visual"]):
                errors.append("visual contains no English word")
            for key in ("title", "emotion"):
                if not re.search(r"[\u3400-\u9fff]", value[key]):
                    errors.append(key+" is not Chinese")
        receipt["validation_errors"] = errors
        if errors:
            raise ValueError("Probe validation failed: " + "; ".join(errors))
        receipt.update(status="succeeded", elapsed_seconds=round(time.perf_counter()-start, 3),
                       result=value, usage=response.get("usage"), timings=response.get("timings"))
        write_json(folder/"receipt.json", receipt)
        print(json.dumps({"status": "succeeded", "receipt": str(folder/"receipt.json"),
                          "result": value, "elapsed_seconds": receipt["elapsed_seconds"]}, ensure_ascii=False), flush=True)
    except Exception as exc:
        receipt.update(status="failed", error=str(exc), elapsed_seconds=round(time.perf_counter()-start, 3))
        write_json(folder/"receipt.json", receipt)
        raise

if __name__ == "__main__":
    main()

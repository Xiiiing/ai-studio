"""Loopback API consent is explicit, revisioned and origin constrained."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from factory import cloud_budget, store, web
from test_factory_cloud_budget import isolated, line_for, request_for


def test_cloud_api_quote_consent_cas_and_cross_origin(isolated, monkeypatch):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = cloud_budget.prepare_batch(p, profile, [line])
    settings = {"video_profiles": {"cloud": {k: v for k, v in profile.items() if k != "profile"}},
                "video_roles": {"video_generator": "cloud"}}
    monkeypatch.setattr(web, "load_config", lambda: settings)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        client.request("GET" if body is None else "POST", "/api/factory/projects/" + p["id"] + path,
                       body=None if body is None else json.dumps(body),
                       headers={"Content-Type": "application/json", **(headers or {})})
        response = client.getresponse()
        result = response.status, json.loads(response.read())
        client.close()
        return result
    try:
        code, ledger = request("/cloud-budget")
        assert code == 200 and ledger["quote"]["id"] == batch["id"]
        payload = {"batch_id": batch["id"], "expected_revision": store.get(p["id"])["revision"],
                   "max_submissions": 1, "max_estimated_cost_micros": 1000000}
        assert request("/cloud-authorize", payload, {"Origin": "https://evil.invalid"})[0] == 403
        assert cloud_budget.summary(p["id"])["authorizations"] == []
        assert request("/cloud-authorize", payload)[0] == 202
        assert request("/cloud-authorize", payload)[0] == 202
        assert request("/cloud-authorize", {**payload, "max_submissions": 2})[0] == 409
        assert request("/cloud-authorize", {**payload, "actor": "validation_agent"})[0] == 409
        cloud_budget.reserve(p["id"], batch["id"], line, profile, request_for(line))
        confirmed = {"intent_id": line["intent_id"], "amount_micros": 1200000,
                     "currency": "CNY", "evidence": "fixture invoice"}
        assert request("/cloud-confirm-cost", confirmed)[0] == 202
        assert request("/cloud-budget")[1]["totals"]["CNY"]["confirmed_cost_micros"] == 1200000
        assert "cloud_budget" not in request("")[1]  # Preserve the old read contract.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

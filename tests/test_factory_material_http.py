"""Real loopback upload, consent and read access boundaries."""
import http.client
import json
import threading
import base64
import io
import random
from PIL import Image
from http.server import ThreadingHTTPServer

import pytest

from factory import web, store
from studio import core, web as media_web
from test_factory_material_library import project, material, picture


@pytest.fixture
def endpoint(project, monkeypatch):
    monkeypatch.setattr(web, "ROOT", core.ROOT)
    monkeypatch.setattr(media_web, "ROOT", core.ROOT)
    class Quiet(web.Handler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True); thread.start()
    def request(method, path, payload=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request(method, "/api/factory" + path, body=json.dumps(payload).encode() if payload is not None else None,
                     headers={"Content-Type": "application/json", **headers})
        response = conn.getresponse(); body = response.read(); status = response.status; conn.close()
        return status, json.loads(body)
    try: yield request
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_real_upload_and_fixed_version_api(endpoint, project):
    code, response = endpoint("POST", "/library", {"action": "create", "kind": "character", "document": {"name": "林夏"}})
    assert code == 201
    asset = response["asset"]
    image = Image.frombytes("RGB", (256, 256), random.Random(1).randbytes(256*256*3))
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    assert len(encoded) > 65536
    code, response = endpoint("POST", "/library", {"action": "upload", "asset_id": asset["asset_id"], "expected_version": 1, "image_base64": encoded})
    assert code == 201 and response["asset"]["version"] == 2
    assert endpoint("GET", "/library/assets/" + asset["asset_id"] + "?version=1")[1]["data"]["images"] == []
    assert endpoint("GET", "/library?q=" + "Lin")[0] == 200


def test_other_drama_private_assets_are_not_visible_without_context(endpoint, project):
    asset = material(project)
    assert endpoint("GET", "/library")[1]["assets"] == []
    assert endpoint("GET", "/library/assets/" + asset["asset_id"])[0] == 400
    assert endpoint("GET", "/library/assets/" + asset["asset_id"] + "?project_id=" + project["id"])[0] == 200


def test_upload_and_financial_routes_keep_origin_guards(endpoint):
    for path in ("/library", "/library/tasks", "/library/tasks/mt_" + "a" * 32):
        assert endpoint("POST", path, {}, Origin="https://unrelated.example")[0] == 403
    code, _ = endpoint("POST", "/library", {"action": "create", "kind": "character", "document": {"name": "人"}})
    assert code == 201


def test_invalid_image_is_a_user_error_and_no_revision_is_published(endpoint):
    asset = material()
    code, response = endpoint("POST", "/library", {"action": "upload", "asset_id": asset["asset_id"], "expected_version": asset["version"], "image_base64": "bm90IGFuIGltYWdl"})
    assert code == 409 and "图片" in response["error"]
    assert endpoint("GET", "/library/assets/" + asset["asset_id"])[1]["version"] == asset["version"]


def test_project_can_be_created_for_material_preparation_without_racing_worker(endpoint):
    code, response = endpoint("POST", "/projects", {"brief": "一个成年人在车站重逢的原创故事", "prepare_materials": True})
    project = response["project"]
    assert code == 202 and project["status"] == "paused" and project["material_setup_requested"] is True
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE project_id=?", (project["id"],)).fetchone()

"""Full-inventory search and HTTP query contracts against isolated SQLite only."""

import copy
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from factory import config, environment, operator_console as console, review_advice, store


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DATABASE", tmp_path / "search.sqlite3")
    monkeypatch.setattr(config, "load_config", lambda: {"llm_roles": {}})
    monkeypatch.setattr(review_advice, "load_config", lambda: {"llm_roles": {}})
    store.initialize()

    def seed(items):
        with store.connection() as db:
            for index, fields in enumerate(items):
                project = {
                    "id": f"{index + 1:012x}", "title": f"项目 {index}",
                    "brief": "成年邻居寻找钥匙的原创故事", "status": "queued", "stage": "queued",
                    "revision": 1, "shots": [], "decisions": [], "budget": {"spent_gpu_minutes": 2},
                    "team_mode": "guided", **copy.deepcopy(fields),
                }
                db.execute("INSERT INTO projects VALUES(?,?,?,?)", (
                    project["id"], index, 1, json.dumps(project, ensure_ascii=False),
                ))
    return seed


@pytest.fixture
def endpoint(inventory, monkeypatch):
    # Importing web normally loads runtime.env; prevent that import side effect.
    monkeypatch.setattr(environment, "load_environment", lambda: None)
    from factory import web
    monkeypatch.setattr(web, "snapshot", lambda: {"worker": {"available": False}})

    class Quiet(web.Handler):
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()

    def request(path):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/api/factory/" + path)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        yield request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_search_reaches_older_projects_and_paginates_matches(inventory):
    inventory([{"title": f"{'匹配' if index % 2 == 0 else '其他'}-{index}"} for index in range(220)])
    latest = console.dashboard()
    assert latest["pagination"]["total"] == 220 and len(latest["projects"]) == 100
    old = console.dashboard(q="匹配-0")
    assert [p["title"] for p in old["projects"]] == ["匹配-0"]
    assert old["pagination"]["total"] == 1 and not old["bounded"]

    first = console.dashboard(q="匹配", limit=100)
    second = console.dashboard(q="匹配", offset=100, limit=100)
    assert first["pagination"] == {"offset": 0, "limit": 100, "total": 110, "has_more": True, "next_offset": 100}
    assert first["bounded"] and second["bounded"]
    assert len(second["projects"]) == 10 and not second["pagination"]["has_more"]
    assert [p["title"] for p in first["projects"] + second["projects"]] == [f"匹配-{index}" for index in range(218, -1, -2)]
    past_end = console.dashboard(q="匹配", offset=110)
    assert past_end["projects"] == [] and not past_end["pagination"]["has_more"]


@pytest.mark.parametrize("query,expected", [
    ("  MiXeD  ", "abcdef123456"), ("strasse", "abcdef123456"),
    ("夜雨", "abcdef123456"), ("BEEF", "abcdef123456"), ("ABCDEF123456", "abcdef123456"),
    ("\t \n", None), ("不存在", "missing"),
])
def test_search_normalizes_case_whitespace_and_matches_all_fields(inventory, query, expected):
    inventory([
        {"id": "abcdef123456", "title": "Mixed Straße", "brief": "夜雨中的 Beef 邻里故事"},
        {"title": "其他作品"},
    ])
    result = console.dashboard(q=query)
    ids = [p["id"] for p in result["projects"]]
    assert ids == (["000000000002", "abcdef123456"] if expected is None else [] if expected == "missing" else [expected])
    assert result["pagination"]["total"] == len(ids)


def test_filters_preserve_existing_status_and_blocking_semantics(inventory):
    statuses = ["queued", "producing", "waiting_remote", "failed", "blocked", "paused", "approved", "cancelled"]
    inventory([{"title": status, "status": status} for status in statuses] + [
        {"title": "validation", "status": "approved", "is_validation": True},
        {"title": "pause_requested", "pause_requested": True},
    ])
    names = lambda mode: {p["title"] for p in console.dashboard(filter=mode)["projects"]}
    assert names("active") == {"queued", "producing", "waiting_remote", "failed", "pause_requested"}
    assert names("attention") == {"failed", "blocked"}
    assert names("delivered") == {"approved", "validation"}
    assert names("all") == set(statuses) | {"validation", "pause_requested"}
    result = console.dashboard(q="approved", filter="delivered", limit=1)
    assert result["pagination"]["total"] == 1 and result["projects"][0]["title"] == "approved"
    assert console.dashboard(q="approved", filter="attention")["pagination"]["total"] == 0
    assert console.dashboard() == console.dashboard(q="", filter="all")


def test_filter_is_applied_before_pagination(inventory):
    inventory([{"status": "approved" if index % 2 == 0 else "blocked"} for index in range(210)])
    first = console.dashboard(filter="delivered")
    second = console.dashboard(filter="delivered", offset=100)
    assert first["pagination"]["total"] == second["pagination"]["total"] == 105
    assert len(first["projects"]) == 100 and len(second["projects"]) == 5
    assert first["pagination"]["next_offset"] == 100 and second["pagination"]["next_offset"] is None
    assert all(p["status"] == "approved" for p in first["projects"] + second["projects"])
    assert not ({p["id"] for p in first["projects"]} & {p["id"] for p in second["projects"]})
    assert first["metrics"]["attention"] == 105 and first["attention_pagination"]["total"] == 105


def test_filtered_pagination_leaves_global_statistics_and_inbox_unchanged(inventory):
    inventory([
        {"status": "blocked", "title": "blocked one"},
        {"status": "failed", "title": "blocked two"},
        {"status": "approved", "title": "delivered"},
        {"status": "producing", "title": "running", "production": {"phase": "waiting_resources"}},
        {"status": "waiting_remote", "title": "remote"},
    ])
    with store.connection() as db:
        db.execute("INSERT INTO jobs(id,project_id,status,created,started) VALUES(?,?,?,?,?)",
                   ("a" * 32, "000000000004", "running", 1, 1))
    all_projects = console.dashboard(attention_offset=1, attention_limit=1)
    selected = console.dashboard(q="one", filter="attention", attention_offset=1, attention_limit=1)
    assert [p["title"] for p in selected["projects"]] == ["blocked one"]
    assert selected["pagination"]["total"] == 1
    for field in ("metrics", "attention_projects", "attention_pagination", "attention_breakdown", "recent_deliveries", "queue", "activity"):
        assert selected[field] == all_projects[field]
    assert selected["metrics"]["projects"] == 5 and selected["metrics"]["attention"] == 2
    assert selected["metrics"]["delivered"] == 1 and selected["metrics"]["running"] == 1
    assert selected["metrics"]["gpu_minutes"] == 10
    assert [task["project_id"] for task in selected["running_tasks"]] == ["000000000004"]
    assert selected["running_tasks"][0]["title"] == all_projects["running_tasks"][0]["title"]


@pytest.mark.parametrize("params", [
    {"q": "x" * 201}, {"q": None}, {"q": 1}, {"filter": "unknown"}, {"filter": ""}, {"filter": []},
    {"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 101}, {"attention_offset": -1}, {"attention_limit": 0},
])
def test_dashboard_rejects_invalid_queries_without_reading_storage(monkeypatch, params):
    def unexpected_read():
        pytest.fail("invalid query must be rejected before opening storage")
    monkeypatch.setattr(store, "connection", unexpected_read)
    with pytest.raises(ValueError):
        console.dashboard(**params)


@pytest.mark.parametrize("route", ["console", "overview"])
def test_real_http_aliases_search_full_inventory(endpoint, inventory, route):
    inventory([{"title": "Early NEEDLE 邻里故事", "status": "approved"}] + [{"title": f"项目 {n}"} for n in range(105)])
    query = urlencode({"q": "  needle 邻里 ", "filter": "delivered", "limit": 1})
    code, result = endpoint(route + "?" + query)
    assert code == 200 and [p["title"] for p in result["projects"]] == ["Early NEEDLE 邻里故事"]
    assert result["pagination"]["total"] == 1 and result["metrics"]["projects"] == 106
    assert result["schema"] == "ai-studio.console.v1" and "capabilities" in result
    assert endpoint(route + "?q=&filter=all")[1]["pagination"]["total"] == 106
    assert endpoint(route + "?" + urlencode({"q": "字" * 200}))[0] == 200


@pytest.mark.parametrize("query", [
    "q=a&q=b", "q=&q=a", "filter=all&filter=active", "filter=unknown", "filter=", "filter=ALL",
    "q=" + "x" * 201, "unknown=1", "unknown=", "offset=0&offset=1", "offset=-1", "offset=",
    "limit=abc", "limit=0", "limit=101", "offset=1000000000", "attention_limit=0", "attention_offset=-1",
])
@pytest.mark.parametrize("route", ["console", "overview"])
def test_real_http_rejects_ambiguous_or_invalid_queries(endpoint, route, query):
    code, result = endpoint(route + "?" + query)
    assert code == 400 and result["error"]

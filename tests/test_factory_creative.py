"""Offline maintenance gates for the autonomous creative pipeline.

Run from the project root:
    envs/factory/bin/python -B tests/test_factory_creative.py

All generated records live in a TemporaryDirectory. LLM calls, DNS and sockets
are replaced locally; this suite never invokes a model, GPU or public network.
The fixtures test contracts and recovery, not the artistic quality of a model.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import creative, research, schemas


def fixture(schema):
    """Produce schema-shaped data without a language model."""
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {key: fixture(value) for key, value in schema["properties"].items()}
    if kind == "array":
        return [fixture(schema["items"]) for _ in range(schema.get("minItems", 0))]
    if kind == "integer":
        return 8
    if kind == "number":
        return .2
    if kind == "boolean":
        return False
    if schema.get("pattern", "").startswith("^#"):
        return "#123456"
    if schema.get("pattern") == r"[\u3400-\u9fff]":
        return "人物发现新的剧情线索"
    return ("abcdefghijklmnopqrstuvwxyz" * 100)[:max(1, schema.get("minLength", 1))]


def major_issue():
    return {"severity": "major", "code": "producer_direction",
            "description": "保留了出品人明确要求删除的宠物支线",
            "evidence": "第一镜仍提及豆豆项圈", "suggestion": "改为同一晚归还钥匙的闭合故事",
            "shot_ids": ["S01"]}


class CreativeContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="factory-creative-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self._patch("factory.schemas.ROOT", self.root)
        self.model = self._patch("factory.llm.json_call", side_effect=self.reply)
        self.dns = self._patch("socket.getaddrinfo",
                              side_effect=AssertionError("Real DNS is forbidden in this suite"))
        self.socket = self._patch("socket.create_connection",
                                 side_effect=AssertionError("Real sockets are forbidden in this suite"))
        self.project = {
            "id": "111122223333", "slug": "factory-111122223333", "title": "夜班钥匙",
            "brief": "面向普通中文观众的城市夜班温情原创短剧",
            "format": "motion_comic", "duration_seconds": 60, "creation_mode": "original",
            "proposal_generation": 1, "script_generation": 1, "sources": [],
        }
        self.queries = ["城市夜班 善意 温情", "温情短剧 人物关系 叙事"]
        self.config = {"llm": {"model": "offline-fixture", "base_url": "http://no-call.invalid"}}
        self.calls = []
        self.blocked_proposals = {"A"}
        self.block_first_script_review = False
        self.script_reviews = 0
        self.brief_reviews = 0
        self.reject_brief_reviews = 0

    def tearDown(self):
        self.dns.assert_not_called()
        self.socket.assert_not_called()

    def _patch(self, target, *args, **kwargs):
        handle = patch(target, *args, **kwargs)
        result = handle.start()
        self.addCleanup(handle.stop)
        return result

    def reply(self, **request):
        self.calls.append(copy.deepcopy(request))
        schema, payload = request["schema"], request["payload"]
        value = fixture(schema)
        properties = schema["properties"]
        if "queries" in properties:
            return {"queries": list(self.queries)}
        if "proposals" in properties:
            for index, item in enumerate(value["proposals"]):
                item["title"] = "候选故事" + str(index)
                item["characters"][0]["role"] = "female"
                item["characters"][1]["role"] = "male"
        elif "reviews" in properties:
            for index, item in enumerate(value["reviews"]):
                item["proposal_id"] = "ABC"[index]
                if item["proposal_id"] in self.blocked_proposals:
                    item["issues"] = [major_issue()]
        elif "causal_beats" in properties:
            instructions = payload["producer_feedback"]["instructions"]
            primary = instructions[0]
            quote = primary["content"] if isinstance(primary["content"], str) else json.dumps(primary["content"], ensure_ascii=False)
            value["characters"][0]["role"] = "female"
            value["characters"][1]["role"] = "male"
            value["requirements"] = [{"instruction": "依据出品人反馈完成新的闭合故事",
                "source_id": primary["source"], "source_quote": quote[:120]}]
            value["excluded_elements"] = [{"element": "出品人明确删除的支线",
                "source_id": primary["source"], "source_quote": quote[:200],
                "forbidden_terms": ["豆豆", "项圈"]}]
        elif "checks" in properties:
            self.brief_reviews += 1
            evidence_id = payload["evidence_catalog"][0]["id"]
            for check in list(value["checks"].values()) + list(value["requirements_review"].values()):
                check["evidence_ids"] = [evidence_id]
            value["originality"]["evidence_ids"] = [evidence_id]
            if self.brief_reviews <= self.reject_brief_reviews:
                value["status"] = "fail"
                value["checks"]["causal_chain"]["status"] = "fail"
                value["checks"]["causal_chain"]["reason"] = "物品位置转移没有明确的行为解释"
            else:
                value["status"] = "pass"
        elif "shots" in properties:
            for shot in value["shots"]:
                shot["dialogue"] = [{"role": "female", "text": "钥匙还给你",
                                     "start": .2, "emotion": "平静"}]
            if self.block_first_script_review and not self.script_reviews:
                value["shots"][0]["action"] = "女主看着豆豆项圈"
        elif "observations" in properties:
            usable = payload["sources"]
            value["observations"] = []
            if usable:
                value["observations"].append({
                    "finding": "公开标题提及夜班生活或短剧叙事",
                    "source_ids": [usable[0]["id"]], "limitation": "摘要未核验流行程度"})
            value["observations"].append({
                "finding": "这一条故意使用无效来源编号",
                "source_ids": ["SRC-does-not-exist"], "limitation": "测试无效引述过滤"})
        else:
            self.script_reviews += 1
            if self.block_first_script_review and self.script_reviews == 1:
                value["issues"] = [major_issue()]
        return value

    @staticmethod
    def feed(extra_unrelated=True):
        items = [
            ("城市夜班 善意互助 新故事", "https://example.com/news-a", "温情短剧讲述夜班生活"),
            ("温情短剧 人物关系 叙事方法", "https://example.com/news-b", "城市生活与互助"),
        ]
        if extra_unrelated:
            items += [
                ("城市夜班 dictionary definitions", "https://dictionary.cambridge.org/word", ""),
                ("天气预报日常播报", "https://example.com/weather", ""),
            ]
        return ("<rss><channel>" + "".join(
            "<item><title>%s</title><link>%s</link><description>%s</description></item>" % item
            for item in items) + "</channel></rss>").encode()

    def mock_feed(self, body=None):
        body = self.feed() if body is None else body
        return self._patch("factory.research.trusted_rss_get",
                           side_effect=lambda url, **_: (body, "application/rss+xml", url))

    def approved_project(self):
        proposal = fixture(schemas.PROPOSAL_ITEM_SCHEMA)
        proposal.update(id="A", eligible=True)
        proposal["characters"][0]["role"] = "female"
        proposal["characters"][1]["role"] = "male"
        return {**self.project, "selected_story": "A", "proposals": [proposal]}

    def test_eligible_choices_preserve_failed_review_and_all_failed_blocks(self):
        result = creative.create_proposals(self.project, self.config, None)
        self.assertIsNone(result["needs_decision"])
        self.assertEqual([p["eligible"] for p in result["proposals"]], [False, True, True])
        self.assertEqual(result["creation_review"]["eligible_count"], 2)
        self.assertFalse(result["creation_review"]["all_passed"])
        self.assertEqual(result["proposals"][0]["review"]["issues"][0], major_issue())
        with self.assertRaises(schemas.CreativeReviewError):
            creative.create_script_and_styles(
                {**self.project, **result, "selected_story": "A"}, self.config, None)
        self.blocked_proposals = set("ABC")
        result = creative.create_proposals(
            {**self.project, "proposal_generation": 2}, self.config, None)
        self.assertEqual(result["creation_review"]["eligible_count"], 0)
        self.assertFalse(result["creation_review"]["passed"])
        self.assertIsNotNone(result["needs_decision"])

    def test_keywords_use_cached_model_plan_instead_of_raw_brief(self):
        queries = research._queries(self.project, self.config)
        self.assertEqual(queries, self.queries)
        request = self.model.call_args.kwargs
        self.assertEqual(request["role"], "researcher")
        self.assertEqual(request["payload"]["brief"], self.project["brief"])
        self.assertNotIn(self.project["brief"], queries)
        self.assertTrue(all(8 <= len(query) <= 25 for query in queries))
        count = self.model.call_count
        self.assertEqual(research._queries(self.project, self.config), queries)
        self.assertEqual(self.model.call_count, count)
        research._queries({**self.project, "proposal_generation": 2}, self.config)
        self.assertEqual(self.model.call_count, count + 1)

    def test_fixed_rss_hosts_redirects_and_size_limits(self):
        good = "https://news.google.com/rss/search?q=test"
        for url in (good, "https://www.bing.com/search?q=test&format=rss",
                    "https://cn.bing.com/search?q=test&format=rss"):
            self.assertEqual(research._trusted_rss_url(url), url)
        for url in ("http://news.google.com/rss/search", "https://news.google.com/other",
                    "https://news.google.com.evil.test/rss/search",
                    "https://u:p@news.google.com/rss/search", "https://127.0.0.1/rss/search",
                    "https://cn.bing.com/other", "https://www.bing.com:8443/search"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                research._trusted_rss_url(url)
        handler = research._TrustedRSSRedirect()
        request = urllib.request.Request("https://www.bing.com/search?q=test&format=rss")
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://cn.bing.com/search?q=test&format=rss")
        self.assertEqual(urlsplit(redirected.full_url).hostname, "cn.bing.com")
        for target in ("https://evil.test/search", "http://cn.bing.com/search",
                       "/rss/articles/example", "https://127.0.0.1/search"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = good
        response.headers = {"Content-Type": "application/rss+xml"}
        response.read.return_value = b"<rss/>"
        opener = MagicMock()
        opener.open.return_value = response
        factory = self._patch("factory.research.urllib.request.build_opener", return_value=opener)
        self.assertEqual(research.trusted_rss_get(good, timeout=100)[0], b"<rss/>")
        self.assertIsInstance(factory.call_args.args[0], research._TrustedRSSRedirect)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)
        response.read.assert_called_once_with(research.MAX_RESPONSE_BYTES + 1)
        response.headers["Content-Length"] = str(research.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(ValueError):
            research.trusted_rss_get(good)

    def test_arbitrary_material_keeps_pinned_transport_and_private_dns_block(self):
        with patch("factory.research.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaisesRegex(ValueError, "非公网"):
                research.safe_public_get("https://public-name.example/material")
        trusted = self._patch("factory.research.trusted_rss_get",
                              side_effect=AssertionError("Arbitrary material cannot use RSS proxy"))
        public = self._patch("factory.research.safe_public_get",
                             return_value=(b"Provided public material", "text/plain", "https://example.com/material"))
        values, errors = research._provided_items(
            [{"url": "https://example.com/material", "rights": "owned"}], "now")
        public.assert_called_once_with("https://example.com/material")
        trusted.assert_not_called()
        self.assertFalse(errors)
        self.assertEqual(values[0]["rights"], "owned")
        for url in ("file:///etc/passwd", "https://localhost/", "https://192.168.1.1/",
                    "https://user:secret@example.com/", "https://example.com:8080/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                research._validated_url(url)

    def test_chinese_background_relevance_without_hotness_claim(self):
        query = "竖屏短剧 叙事结构 关键帧"
        for title in ("短剧出海", "女性叙事", "竖屏漫剧"):
            with self.subTest(title=title):
                result = research._source_relevance(
                    {"title": title, "summary": "", "url": "https://example.com/news"}, query)
                self.assertEqual(result["status"], "background_related")
                self.assertTrue(result["evidence_eligible"])
                self.assertTrue(result["matched_terms"])
                self.assertIs(result["trend_evidence"], False)
        dictionary = research._source_relevance(
            {"title": "短剧 dictionary", "url": "https://dictionary.cambridge.org/a"}, query)
        self.assertFalse(dictionary["evidence_eligible"])
        unrelated = research._source_relevance(
            {"title": "天气预报", "url": "https://example.com/weather"}, query)
        self.assertFalse(unrelated["evidence_eligible"])

    def test_research_excludes_dictionary_and_discards_fabricated_source_ids(self):
        network = self.mock_feed()
        result = research.research_brief(self.project, self.config, None)
        self.assertEqual(result["research"]["relevant_search_source_count"], 2)
        self.assertEqual(result["research"]["trend_verification"], "unverified")
        self.assertEqual(len(result["research"]["observations"]), 1)
        known_ids = {source["id"] for source in result["sources"]}
        for observation in result["research"]["observations"]:
            self.assertLessEqual(set(observation["source_ids"]), known_ids)
        synthesis = [call for call in self.calls if "observations" in call["schema"]["properties"]][-1]
        self.assertTrue(all("dictionary" not in source["url"] and "weather" not in source["url"]
                            for source in synthesis["payload"]["sources"]))
        self.assertTrue(any("不存在" in error["error"] for error in result["research"]["errors"]))
        self.assertTrue(result["research"]["excluded_sources"])
        for request in network.call_args_list:
            self.assertIn(parse_qs(urlsplit(request.args[0]).query)["q"][0], self.queries)

    def test_research_generation_archives_exact_prior_evidence_and_low_relevance_degrades(self):
        network = self.mock_feed()
        research.research_brief(self.project, self.config, None)
        folder = schemas.planning_dir(self.project)
        old = {name: (folder / name).read_bytes()
               for name in ("research.json", "research.md", "sources.json")}
        calls = network.call_count
        research.research_brief(self.project, self.config, None)
        self.assertEqual(network.call_count, calls)
        research.research_brief({**self.project, "proposal_generation": 2}, self.config, None)
        archives = list((folder / "research_versions").glob("v1-*"))
        self.assertTrue(archives)
        for archive in archives:
            for name, content in old.items():
                self.assertEqual((archive / name).read_bytes(), content)
        self.assertGreater(network.call_count, calls)
        low_feed = ("<rss><channel><item><title>城市夜班 温情故事</title>"
                    "<link>https://example.com/one</link></item><item><title>天气预报</title>"
                    "<link>https://example.com/weather</link></item></channel></rss>").encode()
        network.side_effect = lambda url, **_: (low_feed, "application/rss+xml", url)
        result = research.research_brief(
            {**self.project, "proposal_generation": 3}, self.config, None)
        self.assertEqual(result["research"]["status"], "degraded")

    def test_producer_feedback_reaches_writer_and_critic_and_invalidates_cache(self):
        self.block_first_script_review = True
        project = {**self.approved_project(),
            "story_feedback": "删走失宠物支线，仅同一晚遗落钥匙-拾到-归还闭环",
            "script_feedback": "只保留钥匙，不要豆豆项圈"}
        project["episode"] = {"synopsis": "必须从新稿删除的豆豆项圈旧支线", "shots": []}
        result = creative.create_script_and_styles(project, self.config, None)
        self.assertIsNone(result["needs_decision"])
        self.assertEqual(len(result["script_review"]["rounds"]), 2)
        screenplay_calls = [call for call in self.calls if call["role"] == "screenwriter"
                            or (call["role"] == "independent_critic" and "episode" in call["payload"])]
        self.assertEqual(len(screenplay_calls), 4)
        for call in screenplay_calls:
            if call["role"] == "screenwriter":
                self.assertIsNone(call["payload"]["selected_proposal"])
                self.assertIsNone(call["payload"]["previous_episode"])
                if call["payload"].get("independent_feedback"):
                    self.assertIn("previous_attempt", call["payload"])
                    self.assertEqual(len(call["payload"]["previous_attempt"]["shots"]), 15)
                else:
                    self.assertNotIn("previous_attempt", call["payload"])
            self.assertTrue(call["payload"]["resolved_story_brief"]["requirements"])
            scope = call["payload"]["creative_scope"]
            self.assertEqual(scope["hard_fields"], ["requirements", "excluded_elements"])
            self.assertIn("creative_plan", scope["working_plan_fields"])
            self.assertTrue(scope["allow_restructure_second_half"])
            directions = call["payload"]["producer_feedback"]
            self.assertEqual(directions["priority_order"],
                             ["revision_feedback", "script_feedback", "story_feedback", "selected_proposal"])
            content = {entry["source"]: entry["content"] for entry in directions["instructions"]}
            self.assertEqual(content["story_feedback"], project["story_feedback"])
            self.assertEqual(content["script_feedback"], project["script_feedback"])
            self.assertTrue(call["payload"]["proposal_reference_only_where_feedback_does_not_override"])
        before = self.model.call_count
        creative.create_script_and_styles(project, self.config, None)
        self.assertEqual(self.model.call_count, before)
        changed = {**project, "script_feedback": "同一晚归还钥匙后，两人各自回家"}
        creative.create_script_and_styles(changed, self.config, None)
        self.assertGreater(self.model.call_count, before)
        explicit = {"shot_ids": ["S02"], "notes": "第二镜优先用门口环境线索说明动机"}
        revised = creative.revise_script(
            {**project, **result, "script_generation": 2}, self.config, None, explicit)
        self.assertEqual(revised["episode"]["shots"][0], result["episode"]["shots"][0])
        for call in self.calls[-2:]:
            # The final two calls are the writer and independent critic for a local edit.
            self.assertIn(call["role"], ("screenwriter", "independent_critic"))
            self.assertEqual(call["payload"]["producer_feedback"]["instructions"][0],
                             {"source": "revision_feedback", "content": explicit})
        saved = json.loads((schemas.planning_dir(project) / "script-review.json").read_text())
        self.assertEqual(saved["producer_feedback"]["instructions"][0]["content"], explicit)


    def test_source_backed_deletion_blocks_even_if_critic_claims_pass(self):
        original = self.reply
        def still_violates(**request):
            value = original(**request)
            if "shots" in request["schema"]["properties"]:
                value["shots"][0]["action"] = "女主还拿着豆豆项圈"
            return value
        self.model.side_effect = still_violates
        project = {**self.approved_project(),
            "story_feedback": "删去豆豆项圈和宠物走失的支线"}
        result = creative.create_script_and_styles(project, self.config, None)
        self.assertIsNotNone(result["needs_decision"])
        self.assertFalse(result["script_review"]["passed"])
        self.assertEqual(len(result["script_review"]["rounds"]), 4)
        self.assertTrue(any(issue["code"] == "producer_excluded_element"
                            for issue in result["script_review"]["review"]["issues"]))
        # The independent critic fixture returned scores of 8 with no issues.
        self.assertEqual(result["script_review"]["review"]["scores"]["causality"], 8)


    def test_compact_target_respects_explicit_two_round_limit_and_persists_reviews(self):
        self.config["creative"] = {"max_review_rounds": 2}
        for generation, rejected, expected_pass in ((7, 1, True), (8, 3, False)):
            with self.subTest(expected_pass=expected_pass):
                self.brief_reviews = 0
                self.reject_brief_reviews = rejected
                project = {**self.approved_project(), "script_generation": generation,
                           "story_feedback": "删除豆豆项圈，依据出品人意见修订因果，测试版本" + str(generation)}
                before = len(self.calls)
                if expected_pass:
                    result = creative.resolve_story_brief(project, self.config)
                    self.assertTrue(result["resolution_review"]["passed"])
                    self.assertEqual(result["resolution_revision"], 2)
                else:
                    with self.assertRaises(schemas.CreativeReviewError):
                        creative.resolve_story_brief(project, self.config)
                calls = self.calls[before:]
                self.assertEqual(sum(call["role"] == "story_editor" for call in calls), 2)
                self.assertEqual(sum(call["role"] == "story_critic" for call in calls), 2)
                self.assertFalse(any(call["role"] == "screenwriter" for call in calls))
                saved = json.loads((schemas.planning_dir(project) / "resolved-story-review.json").read_text())
                self.assertEqual(saved["passed"], expected_pass)
                self.assertEqual(len(saved["rounds"]), 2)


    def test_review_catalog_ids_reconstruct_only_original_narrative(self):
        from jsonschema import validate, ValidationError
        project = {**self.approved_project(), "script_generation": 9,
                   "story_feedback": "删除豆豆项圈，完成当晚归还闭环"}
        result = creative.resolve_story_brief(project, self.config)
        call = [item for item in self.calls if item["role"] == "story_critic"][-1]
        catalog = call["payload"]["evidence_catalog"]
        self.assertEqual(catalog, creative._resolved_evidence_catalog(call["payload"]["resolved_target"]))
        self.assertFalse(any(item["path"].startswith(("requirements", "excluded_elements"))
                             for item in catalog))
        lookup = {item["id"]: item["text"] for item in catalog}
        review = result["resolution_review"]["rounds"][-1]["review"]
        for name, check in review["checks"].items():
            properties = call["schema"]["properties"]["checks"]["properties"][name]["properties"]
            self.assertNotIn("evidence_quotes", properties)
            self.assertEqual(set(properties["evidence_ids"]["items"]["enum"]), set(lookup))
            self.assertEqual(check["evidence_quotes"], [lookup[ident] for ident in check["evidence_ids"]])
        invalid = fixture(call["schema"])
        invalid["checks"]["causal_chain"]["evidence_ids"] = ["invented-id"]
        with self.assertRaises(ValidationError):
            validate(invalid, call["schema"])
        issue = fixture(call["schema"]["properties"]["issues"]["items"])
        issue["evidence_ids"] = ["invented-id"]
        invalid = fixture(call["schema"])
        invalid["issues"] = [issue]
        with self.assertRaises(ValidationError):
            validate(invalid, call["schema"])
        self.assertTrue((self.root / review["evidence_catalog"]).is_file())

    def test_policy_update_reviews_second_target_without_new_story_or_old_errors(self):
        self.config["creative"] = {"max_review_rounds": 2}
        self.reject_brief_reviews = 1
        project = {**self.approved_project(), "script_generation": 30,
                   "story_feedback": "删除豆豆项圈，仅保留同一晚归还闭环"}
        initial = creative.resolve_story_brief(project, self.config)
        self.assertEqual(initial["resolution_revision"], 2)
        body_keys = ("title", "logline", "synopsis", "ending", "characters", "causal_beats")
        initial_body = {key: initial[key] for key in body_keys}
        for generation, fail in ((31, False), (32, True)):
            with self.subTest(fail=fail):
                folder = schemas.planning_dir(project)
                prior = json.loads((folder / "resolved-story-brief.json").read_text())
                prior["review_policy_signature"] = "old-free-quote-policy"
                prior["resolution_review"]["passed"] = False
                prior["resolution_review"]["old_error"] = "STALE_QUOTE_FORMAT_ONLY"
                (folder / "resolved-story-brief.json").write_text(
                    json.dumps(prior, ensure_ascii=False), encoding="utf-8")
                self.reject_brief_reviews = self.brief_reviews + 1 if fail else 0
                before = len(self.calls)
                if fail:
                    with self.assertRaises(schemas.CreativeReviewError):
                        creative.resolve_story_brief({**project, "script_generation": generation}, self.config)
                else:
                    result = creative.resolve_story_brief(
                        {**project, "script_generation": generation}, self.config)
                    self.assertTrue(result["resolution_review"]["passed"])
                calls = self.calls[before:]
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["role"], "story_critic")
                self.assertNotIn("STALE_QUOTE_FORMAT_ONLY", json.dumps(calls[0]["payload"]))
                self.assertNotIn("resolution_review", calls[0]["payload"]["resolved_target"])
                saved = json.loads((folder / "resolved-story-brief.json").read_text())
                self.assertEqual(saved["resolution_revision"], 2)
                self.assertEqual({key: saved[key] for key in body_keys}, initial_body)
                self.assertTrue(saved["resolution_review"]["policy_reassessment"])
                self.assertTrue((self.root / saved["resolution_review"]["previous_policy_record"]).is_file())
                if fail:
                    count = self.model.call_count
                    with self.assertRaises(schemas.CreativeReviewError):
                        creative.resolve_story_brief(
                            {**project, "script_generation": generation + 1}, self.config)
                    self.assertEqual(self.model.call_count, count)

    def test_target_requirements_and_originality_gate_before_screenplay(self):
        original = self.reply
        for generation, defect in ((40, "originality"), (41, "requirement"), (42, "none")):
            with self.subTest(defect=defect):
                project = {**self.approved_project(), "script_generation": generation,
                    "story_feedback": "删除豆豆项圈；以角色选择完成归还闭环，测试" + str(generation)}
                def evaluate(**request):
                    value = original(**request)
                    if request["role"] == "story_critic":
                        value["originality"]["score"] = 5 if defect == "originality" else 6
                        if defect == "requirement":
                            check = next(iter(value["requirements_review"].values()))
                            check["status"] = "fail"
                            check["reason"] = "正文缺少该条要求的前置线索与选择结果"
                    return value
                self.model.side_effect = evaluate
                before = len(self.calls)
                if defect == "none":
                    result = creative.resolve_story_brief(project, self.config)
                    self.assertTrue(result["resolution_review"]["passed"])
                else:
                    with self.assertRaises(schemas.CreativeReviewError) as raised:
                        creative.resolve_story_brief(project, self.config)
                    self.assertTrue(any(
                        issue["code"] == "resolved_originality_below_minimum" if defect == "originality"
                        else issue["code"].startswith("resolved_requirement_")
                        for issue in raised.exception.issues))
                calls = self.calls[before:]
                expected = 1 if defect == "none" else 4
                self.assertEqual(sum(call["role"] == "story_editor" for call in calls), expected)
                self.assertEqual(sum(call["role"] == "story_critic" for call in calls), expected)
                self.assertFalse(any(call["role"] == "screenwriter" for call in calls))

    def test_old_target_contract_is_archived_and_rebuilt_not_marked_passed(self):
        project = {**self.approved_project(), "script_generation": 50,
                   "story_feedback": "删除豆豆项圈，以新的角色选择完成归还"}
        initial = creative.resolve_story_brief(project, self.config)
        legacy = copy.deepcopy(initial)
        legacy.pop("creative_plan")
        legacy["schema"] = "resolved-story-brief.v2"
        legacy["source_signature"] = "legacy-schema-source-signature"
        legacy["resolution_revision"] = 2
        legacy["resolution_review"]["passed"] = False
        legacy["requirements"][0]["instruction"] = "旧的复合要求" * 30
        folder = schemas.planning_dir(project)
        (folder / "resolved-story-brief.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        before = len(self.calls)
        rebuilt = creative.resolve_story_brief({**project, "script_generation": 51}, self.config)
        calls = self.calls[before:]
        self.assertEqual([call["role"] for call in calls], ["story_editor", "story_critic"])
        self.assertEqual(rebuilt["schema"], "resolved-story-brief.v3")
        self.assertEqual(rebuilt["resolution_revision"], 1)
        self.assertIn("creative_plan", rebuilt)
        archive = self.root / rebuilt["resolution_review"]["previous_policy_record"]
        self.assertEqual(json.loads(archive.read_text()), legacy)
        self.assertNotIn("creative_plan", json.loads(archive.read_text()))
        before = self.model.call_count
        reused = creative.resolve_story_brief({**project, "script_generation": 52}, self.config)
        self.assertEqual(rebuilt, reused)
        self.assertEqual(self.model.call_count, before)

    def test_atomic_requirements_mechanism_and_dialogue_have_hard_schema_bounds(self):
        from jsonschema import validate, ValidationError
        target = fixture(schemas.RESOLVED_STORY_SCHEMA)
        validate(target, schemas.RESOLVED_STORY_SCHEMA)
        no_plan = copy.deepcopy(target)
        no_plan.pop("creative_plan")
        with self.assertRaises(ValidationError):
            validate(no_plan, schemas.RESOLVED_STORY_SCHEMA)
        paragraph = copy.deepcopy(target)
        paragraph["requirements"][0]["instruction"] = "一" * 101
        with self.assertRaises(ValidationError):
            validate(paragraph, schemas.RESOLVED_STORY_SCHEMA)
        proposal_rule = copy.deepcopy(target)
        proposal_rule["requirements"][0]["source_id"] = "selected_proposal"
        with self.assertRaises(ValidationError):
            validate(proposal_rule, schemas.RESOLVED_STORY_SCHEMA)
        schema = schemas.resolved_review_schema(["EVknown"], ["R01", "R02"])
        review = fixture(schema)
        validate(review, schema)
        review["requirements_review"].pop("R02")
        with self.assertRaises(ValidationError):
            validate(review, schema)
        schema = schemas.episode_schema(15)
        episode = fixture(schema)
        episode["shots"][0]["dialogue"] = [{"role": "female", "text": "一二三四五六七八九十",
            "start": .2, "emotion": "平静"}]
        validate(episode, schema)
        episode["shots"][0]["dialogue"][0]["text"] += "！"
        with self.assertRaises(ValidationError):
            validate(episode, schema)
        self.assertEqual(episode["shots"][0]["dialogue"][0]["text"], "一二三四五六七八九十！")

    def test_script_gate_explains_low_scores_without_promoting_minor_notes(self):
        minor = {**major_issue(), "severity": "minor", "code": "optional_note"}
        cases = [
            ({"hook": 8, "causality": 7, "motivation": 7, "originality": 5,
              "closure": 8, "feasibility": 9}, ["score_below_minimum"]),
            ({key: 6 for key in schemas.SCORE_KEYS}, ["score_average_below_minimum"]),
            ({key: 7 for key in schemas.SCORE_KEYS}, []),
        ]
        for scores, codes in cases:
            with self.subTest(scores=scores):
                review = {"scores": scores, "issues": [copy.deepcopy(minor)]}
                found = creative._script_gate_issues(review)
                self.assertEqual([issue["code"] for issue in found], codes)
                self.assertEqual(schemas.review_passes(review), not found)
                self.assertEqual(review["issues"], [minor])
        original = self.reply
        def low_originality(**request):
            value = original(**request)
            if request["role"] == "independent_critic" and "episode" in request["payload"]:
                value["scores"] = copy.deepcopy(cases[0][0])
                value["issues"] = [copy.deepcopy(minor)]
            return value
        self.model.side_effect = low_originality
        result = creative.create_script_and_styles(self.approved_project(), self.config, None)
        self.assertFalse(result["script_review"]["passed"])
        actual = result["needs_decision"]["issues"]
        self.assertEqual([issue["code"] for issue in actual], ["score_below_minimum"])
        self.assertIn("originality", actual[0]["description"])
        self.assertIn("=5", actual[0]["description"])
        self.assertIn("6", actual[0]["description"])
        self.assertEqual(result["script_review"]["review"]["issues"], [minor])
        major = {"scores": {key: 8 for key in schemas.SCORE_KEYS}, "issues": [major_issue()]}
        self.assertEqual(creative._script_gate_issues(major), major["issues"])
        self.assertFalse(schemas.review_passes(major))

    def test_approved_compact_target_reused_only_with_same_sources_policy_and_body(self):
        project = {**self.approved_project(), "script_generation": 20,
                   "story_feedback": "删除豆豆项圈，仅保留当晚归还"}
        first = creative.resolve_story_brief(project, self.config)
        before = self.model.call_count
        second = creative.resolve_story_brief({**project, "script_generation": 21}, self.config)
        self.assertEqual(first, second)
        self.assertEqual(self.model.call_count, before)
        for generation, changed_field, changed_value in (
                (22, "review_policy_signature", "old-policy"),
                (23, "source_signature", "different-source"),
                (24, "resolution_review", {"passed": False}),
                (25, "synopsis", "正文被改动后不能借用旧通过记录")):
            with self.subTest(changed_field=changed_field):
                folder = schemas.planning_dir(project)
                saved = json.loads((folder / "resolved-story-brief.json").read_text())
                saved[changed_field] = changed_value
                (folder / "resolved-story-brief.json").write_text(
                    json.dumps(saved, ensure_ascii=False), encoding="utf-8")
                before = self.model.call_count
                creative.resolve_story_brief({**project, "script_generation": generation}, self.config)
                self.assertGreater(self.model.call_count, before)
        before = self.model.call_count
        creative.resolve_story_brief({**project, "script_generation": 26,
            "story_feedback": "删除豆豆项圈，改为当晚交回门卫登记"}, self.config)
        self.assertGreater(self.model.call_count, before)

    def test_shot_scope_expands_edits_and_excludes_preserved_ranges(self):
        available = ["S%02d" % i for i in range(1, 16)]
        for text, expected in (
            ("仅修改并重排 S07、S08、S09、S10、S11、S12、S13、S14、S15，保留 S01 至 S06 的内容、人物和美术设定。",
             available[6:]),
            ("仅改S07至S15；S01至S06保持不变", available[6:]),
            ("修改S07，S08，S09；不修改S01到S06", available[6:9]),
            ("保持S01-S06不变，调整S07..S15", available[6:]),
            ("保留S01至S06", available[6:]),
            ("参考S03，整体叙事需要更克制", []),
            ("修改S07的结束动作，使其呼应S01的情绪", ["S07"]),
        ):
            with self.subTest(feedback=text):
                self.assertEqual(schemas.revision_shot_ids(text, available), expected)
        self.assertEqual(schemas.revision_shot_ids(
            {"notes": "保留S01至S06", "shot_ids": ["S08", "S07", "S08"]}, available),
            ["S08", "S07"])
        self.model.assert_not_called()

    def test_shot_scope_rejects_conflicts_and_unknown_ids(self):
        available = ["S%02d" % i for i in range(1, 16)]
        for feedback in (
            "修改S01至S07，保留S01至S06",
            "修改S07并保留S07",
            "仅改S99",
            "修改S09至S07",
            "保留S01至S15",
            {"shot_ids": ["S07"], "notes": "保留S07不变"},
            {"shot_ids": ["S07"], "notes": "调整S08"},
            {"shot_ids": ["S99"], "notes": "重做"},
            {"shot_ids": "S07", "notes": "重做"},
            {"shot_ids": [], "notes": "重做"},
            {"shot_ids": ["S07", 8], "notes": "重做"},
        ):
            with self.subTest(feedback=feedback), self.assertRaises(schemas.CreativeReviewError):
                schemas.revision_shot_ids(feedback, available)
        self.model.assert_not_called()

    def test_local_revision_scope_keeps_first_six_despite_model_changes(self):
        raw = fixture(schemas.episode_schema(15))
        for index, shot in enumerate(raw["shots"], 1):
            shot["action"] = "原版已批准的镜头内容%d" % index
            shot["prompt"] = "Previously approved illustrated composition %d." % index
        previous = creative._canonical_episode(raw, self.project)
        feedback = "仅修改并重排 S07、S08、S09、S10、S11、S12、S13、S14、S15，保留 S01 至 S06 的内容、人物和美术设定。"
        project = {**self.approved_project(), "script_generation": 2, "episode": previous,
                   "styles": fixture(schemas.STYLES_SCHEMA)["styles"], "script_feedback": feedback}
        result = creative.create_script_and_styles(project, self.config, None)
        self.assertIsNone(result["needs_decision"])
        self.assertEqual(result["episode"]["shots"][:6], previous["shots"][:6])
        self.assertNotEqual(result["episode"]["shots"][6]["action"], previous["shots"][6]["action"])
        writers = [call for call in self.calls if call["role"] == "screenwriter"]
        self.assertEqual(len(writers), 1)
        self.assertEqual(writers[0]["payload"]["only_revise_shot_ids"],
                         ["S%02d" % i for i in range(7, 16)])
        critics = [call for call in self.calls if call["role"] == "independent_critic"]
        self.assertEqual(critics[0]["payload"]["episode"]["shots"][:6], previous["shots"][:6])

    def test_scoped_revision_compiles_style_without_changing_unaffected_assets(self):
        project = self.approved_project()
        initial = creative.create_script_and_styles(project, self.config, None)
        project.update(copy.deepcopy(initial))
        style = project["styles"][1]
        style.update(prompt="Soft watercolor, muted blue and warm yellow, paper grain, warm steam from food.",
                     name="柔和水彩", palette=["#89C2D9", "#F4A261", "#E0FBFC"])
        project.update(selected_style={"id": style["id"]}, script_generation=2,
                       budget={"gpu_minutes": 120, "max_attempts": 3},
                       production={"gpu_seconds": 1040.537},
                       assets={"characters": {"male": {"path": "retained/male.png"}}})
        for shot in project["shots"]:
            shot.update(status="pending", image={"path": shot["id"]+"/old-image.png"},
                        attempts=[{"take": "take_001", "source_signature": "old"}])
        before = copy.deepcopy(project)
        affected = ["S03", "S05", "S09", "S11", "S13"]
        feedback = {"shot_ids": affected, "notes": "只简化指定镜头构图，保留原故事和台词。"}
        compiled = "Soft watercolor illustration, muted blue and warm yellow palette, paper grain, blended edges and diffuse lighting."
        original = self.reply
        def respond(**request):
            if request["role"] == "prompt_style_compiler":
                self.calls.append(copy.deepcopy(request))
                self.assertEqual(request["payload"]["approved_style"]["prompt"], style["prompt"])
                return {"render_style_prompt": compiled,
                        "retained_visual_traits": ["水彩", "蓝黄配色", "纸张纹理"],
                        "removed_scene_elements": ["食物蒸汽"]}
            return original(**request)
        self.model.side_effect = respond
        revised = creative.revise_script(project, self.config, None, feedback)
        self.assertTrue(revised["script_review"]["passed"])
        self.assertEqual(project, before)
        self.assertEqual(revised["styles"], before["styles"])
        for index, shot in enumerate(revised["episode"]["shots"]):
            if shot["id"] in affected:
                self.assertEqual(shot["render_style_prompt"], compiled)
                self.assertNotIn("food", shot["render_style_prompt"])
                self.assertTrue((self.root / shot["render_style_source"]["artifact"]).is_file())
            else:
                self.assertEqual(shot, before["episode"]["shots"][index])
                self.assertEqual(revised["shots"][index], before["shots"][index])
        compiler_calls = [call for call in self.calls if call["role"] == "prompt_style_compiler"]
        self.assertEqual(len(compiler_calls), 1)
        count = self.model.call_count
        creative.revise_script(project, self.config, None, feedback)
        self.assertEqual(self.model.call_count, count)

    def test_failed_scoped_revision_preserves_approved_style_for_feedback_retry(self):
        project = self.approved_project()
        project.update(creative.create_script_and_styles(project, self.config, None))
        selected = project["styles"][0]
        selected.update(name="已批准水彩", prompt="Approved soft watercolor, paper grain, muted blue and warm yellow.")
        project.update(selected_style={"id": selected["id"]}, script_generation=2,
                       budget={"gpu_minutes": 120, "max_attempts": 3},
                       production={"gpu_seconds": 1040.537},
                       assets={"characters": {"female": {"path": "retained/female.png"}}})
        for shot in project["shots"]:
            shot.update(status="pending", image={"path": shot["id"] + "/approved.png"},
                        attempts=[{"take": "take_001", "source_signature": "approved"}])
        before = copy.deepcopy(project)
        affected = ["S01"]
        feedback = {"shot_ids": affected, "notes": "仅简化第一镜构图，保留已批准画风与其他镜头。"}
        original = self.reply
        reject = True
        compiled = "Soft watercolor illustration, paper grain, muted blue and warm yellow, diffuse lighting."
        def respond(**request):
            if request["role"] == "prompt_style_compiler":
                self.calls.append(copy.deepcopy(request))
                self.assertEqual(request["payload"]["approved_style"]["prompt"], selected["prompt"])
                return {"render_style_prompt": compiled,
                        "retained_visual_traits": ["水彩", "纸张纹理", "蓝黄配色"],
                        "removed_scene_elements": []}
            value = original(**request)
            if reject and request["role"] == "independent_critic" and "episode" in request["payload"]:
                value["issues"] = [major_issue()]
            return value
        self.model.side_effect = respond
        start = len(self.calls)
        revised = creative.revise_script(project, self.config, None, feedback)
        calls = self.calls[start:]
        self.assertEqual(sum(call["role"] == "independent_critic" for call in calls), 4)
        self.assertFalse(revised["script_review"]["passed"])
        self.assertTrue(all(not item["review"]["passed"] for item in revised["script_review"]["rounds"]))
        self.assertEqual(revised["needs_decision"]["kind"], "script_review")
        self.assertEqual(revised["styles"], before["styles"])
        self.assertIsNot(revised["styles"], project["styles"])
        self.assertIsNot(revised["styles"][0]["palette"], project["styles"][0]["palette"])
        folder = schemas.planning_dir(project)
        self.assertEqual(json.loads((folder / "styles.json").read_text()), before["styles"])
        self.assertFalse(any(call["role"] == "visual_designer" for call in calls))
        self.assertEqual(project, before)
        project.update(copy.deepcopy(revised))
        self.assertEqual(project["selected_style"], before["selected_style"])
        for key in ("budget", "production", "assets"):
            self.assertEqual(project[key], before[key])
        for index, shot in enumerate(project["shots"]):
            if shot["id"] not in affected:
                self.assertEqual(shot, before["shots"][index])
        # Apply new feedback as the service does after the failed review. The
        # approved specification must still be available to the style compiler.
        reject = False
        project["script_generation"] = 3
        start = len(self.calls)
        retried = creative.revise_script(project, self.config, None,
            {"shot_ids": affected, "notes": "第一镜只保留门口人物与钥匙交还动作，解决审稿问题。"})
        self.assertTrue(retried["script_review"]["passed"])
        self.assertIsNone(retried["needs_decision"])
        self.assertEqual(retried["styles"], before["styles"])
        self.assertEqual(retried["episode"]["shots"][0]["render_style_prompt"], compiled)
        self.assertTrue((self.root / retried["episode"]["shots"][0]["render_style_source"]["artifact"]).is_file())
        self.assertFalse(any(call["role"] == "visual_designer" for call in self.calls[start:]))
        for index, shot in enumerate(retried["shots"]):
            if shot["id"] not in affected:
                self.assertEqual(shot, before["shots"][index])
        for key in ("budget", "production", "assets"):
            self.assertEqual(project[key], before[key])

    def test_failed_unscoped_creation_does_not_retain_previous_style_candidates(self):
        project = self.approved_project()
        project.update(styles=fixture(schemas.STYLES_SCHEMA)["styles"])
        original = self.reply
        def reject_review(**request):
            value = original(**request)
            if request["role"] == "independent_critic" and "episode" in request["payload"]:
                value["issues"] = [major_issue()]
            return value
        self.model.side_effect = reject_review
        result = creative.create_script_and_styles(project, self.config, None)
        self.assertFalse(result["script_review"]["passed"])
        self.assertEqual(result["styles"], [])
        self.assertEqual(json.loads((schemas.planning_dir(project) / "styles.json").read_text()), [])
        self.assertFalse(any(call["role"] == "visual_designer" for call in self.calls))

    def test_canonical_episode_is_idempotent_without_mutating_input(self):
        for format_name in ("motion_comic", "animation", "live_action"):
            with self.subTest(format=format_name):
                raw = fixture(schemas.episode_schema(15))
                snapshot = copy.deepcopy(raw)
                project = {**self.project, "format": format_name}
                first = creative._canonical_episode(raw, project)
                second = creative._canonical_episode(first, project)
                third = creative._canonical_episode(second, project)
                self.assertEqual(raw, snapshot)
                self.assertEqual(first, second)
                self.assertEqual(second, third)
                self.assertEqual(first["shots"][0]["prompt"].count(
                    "No visible text, captions, logos, or watermarks."), 1)
        self.model.assert_not_called()

    def test_dramatic_beat_requires_chinese_but_action_accepts_english(self):
        from jsonschema import validate, ValidationError
        schema = schemas.episode_schema(15)
        value = fixture(schema)
        value["shots"][0]["action"] = "A woman pauses beside the entrance."
        validate(value, schema)
        value["shots"][0]["dramatic_beat"] = "An English-only story beat"
        with self.assertRaises(ValidationError):
            validate(value, schema)

    def test_version_two_steps_keep_registered_prompts_and_review_evidence(self):
        from factory import creative_runtime as runtime
        project={**self.approved_project(), "workflow_version":2, "style_generation":1}
        project.update(runtime.resolve_target(project,self.config,None))
        project.update(runtime.write_screenplay(project,self.config,None))
        writer_calls=[call for call in self.calls if call["role"]=="screenwriter"]
        self.assertEqual(len(writer_calls),1)
        self._patch("factory.creative.direct_storyboard",side_effect=lambda p,c,e,cb,**kw:{
            "episode":copy.deepcopy(e),"issues":[],"receipt":{"role":"storyboard_director"},
            "artifact":"production/"+p["slug"]+"/planning/director.json"})
        project.update(runtime.direct_screenplay(project,self.config,None))
        reviewed=runtime.review_screenplay(project,self.config,None)
        self.assertTrue(reviewed["script_review"]["passed"])
        self.assertEqual(reviewed["script_complete_generation"],1)
        self.assertTrue(reviewed["script_review"]["rounds"][0]["storyboard"])
        self.assertIn("script-review-v1-r1-",reviewed["planning_artifacts"]["script_review"])
        project.update(reviewed)
        designed=runtime.design_visuals(project,self.config,None)
        self.assertEqual(len(designed["styles"]),3)
        before=self.model.call_count
        runtime.write_screenplay(project,self.config,None)
        self.assertEqual(self.model.call_count,before)

    def test_version_two_scoped_feedback_locks_unselected_shots_and_cast(self):
        from factory import creative_runtime as runtime
        project=self.approved_project()
        project.update(creative.create_script_and_styles(project,self.config,None))
        project['episode']['shots'][0]['action']='锁定镜头的原有动作，不允许改动'
        project['script']=copy.deepcopy(project['episode'])
        previous=copy.deepcopy(project['episode'])
        project.update(workflow_version=2,script_generation=2,
                       script_feedback={'notes':'只调整第三镜动作，其他镜头保持原样','shot_ids':['S03']})
        count=self.model.call_count
        resolved=runtime.resolve_target(project,self.config,None)
        self.assertTrue(resolved['story_resolution_skipped'])
        self.assertEqual(self.model.call_count,count)
        project.update(resolved)
        drafted=runtime.write_screenplay(project,self.config,None)
        for shot in drafted['screenplay_draft']['shots']:
            if shot['id']!='S03':
                self.assertEqual(shot,previous['shots'][int(shot['id'][1:])-1])
        self.assertEqual(drafted['screenplay_draft']['cast'],previous['cast'])
        self.assertEqual(drafted['screenplay_draft']['visual_style'],previous['visual_style'])
        latest=[call for call in self.calls if call['role']=='screenwriter'][-1]
        self.assertEqual(latest['payload']['only_revise_shot_ids'],['S03'])
        self.assertFalse(latest['payload']['creative_scope']['allow_restructure_second_half'])
        project.update(drafted)
        directed=[]
        self._patch('factory.creative.direct_storyboard',side_effect=lambda p,c,e,cb,**kw:(
            directed.append(kw['shot_ids']) or {'episode':e,'issues':[],
                'receipt':{'role':'storyboard_director'},'artifact':'director.json'}))
        runtime.direct_screenplay(project,self.config,None)
        self.assertEqual(directed,[['S03']])


if __name__ == "__main__":
    unittest.main(verbosity=2)

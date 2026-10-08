"""位置工具：读取用户位置、附近地点、按位置规划路线，以及接入 /api/chat。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from src.navigation import campus_map, execute_location_tool, parse_user_location


def at(poi_id, source="gps", accuracy=15):
    p = campus_map().pois[poi_id]
    return {"x": p["x"] + 0.2, "z": p["z"] + 0.2, "source": source, "accuracy": accuracy}


class LocationToolTests(unittest.TestCase):
    def setUp(self):
        self.m = campus_map()

    def test_parse_rejects_bad_location(self):
        self.assertIsNone(parse_user_location(None))
        for bad in ([], {"x": "1", "z": 0}, {"x": 1e9, "z": 0}, {"x": True, "z": 0}, {"x": 0, "z": 0, "source": "ip"}, {"x": 0, "z": 0, "accuracy": -1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_user_location(bad)
        self.assertEqual(parse_user_location({"x": 1, "z": 2})["source"], "gps")

    def test_get_user_location_describes_without_coordinates(self):
        info = execute_location_tool(self.m, "get_user_location", {}, at("library"))
        self.assertTrue(info["available"] and info["on_campus"])
        self.assertEqual(info["nearest_places"][0]["name"], "逸夫图书馆")
        self.assertNotIn('"x"', json.dumps(info))

    def test_missing_location_asks_user_to_share(self):
        for name, args in (("get_user_location", {}), ("find_nearby_places", {"query": "食堂"}), ("plan_campus_route", {"destination": "食堂"})):
            out = execute_location_tool(self.m, name, args, None)
            self.assertFalse(out["ok"])
            self.assertIn("定位", out["message"])

    def test_nearby_sorted_by_walking_distance(self):
        out = execute_location_tool(self.m, "find_nearby_places", {"query": "食堂", "limit": 4}, at("library"))
        self.assertTrue(out["ok"])
        walks = [r["walk_m"] for r in out["results"]]
        self.assertEqual(walks, sorted(walks))
        self.assertTrue(all(r["category"] == "餐饮" for r in out["results"]))

    def test_route_to_group_picks_nearest_from_user(self):
        out = execute_location_tool(self.m, "plan_campus_route", {"destination": "最近的餐厅"}, at("library"))
        best = min(self.m.route({"x": at("library")["x"], "z": at("library")["z"]}, {"poi": mid})["distance_m"] for mid in self.m.groups["dining"]["members"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["distance_m"], best)
        self.assertTrue(out["steps"])

    def test_route_with_named_origin_needs_no_location(self):
        out = execute_location_tool(self.m, "plan_campus_route", {"destination": "逸夫图书馆", "origin": self.m.gates[0]["name"]}, None)
        self.assertTrue(out["ok"])
        self.assertEqual(out["to"], "逸夫图书馆")


def config():
    return {"provider": "custom", "protocol": "openai", "base_url": "https://api.example.invalid/v1", "model": "m", "api_key": "sk-test-not-a-secret", "enabled": True}


class LocationChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app({"TESTING": True, "WORKBENCH_DB": root / "workbench.db", "TOOLS_DB": root / "tools.db"})
        self.client = self.app.test_client()
        self.identity = self.client.post("/api/conversations", json={}).json["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def chat(self, question, **extra):
        return self.client.post("/api/chat", json={"question": question, "session_id": self.identity, **extra})

    def offline(self):
        self.client.post("/api/settings", json={**config(), "enabled": False, "api_key": ""})

    def test_invalid_location_rejected(self):
        self.assertEqual(self.chat("我在哪", location={"x": "a", "z": 0}).status_code, 400)

    def test_offline_where_am_i_and_route(self):
        self.offline()
        where = self.chat("我现在在哪？", location=at("library")).json
        self.assertEqual(where["route"], "location")
        self.assertIn("逸夫图书馆", where["answer"])
        self.assertTrue(where["location_used"])
        route = self.chat("离我最近的食堂怎么走？", location=at("library")).json
        self.assertIn("步行约", route["answer"])
        self.assertTrue(route["map_targets"])
        # 会话记录中不保存原始坐标
        stored = json.dumps(self.client.get("/api/conversations/" + self.identity).json, ensure_ascii=False)
        self.assertNotIn(str(at("library")["x"]), stored)

    def test_offline_without_location_keeps_previous_behavior(self):
        self.offline()
        response = self.chat("我现在在哪？").json
        self.assertNotEqual(response.get("route"), "location")
        self.assertFalse(response["location_used"])

    def test_model_reads_location_via_tool(self):
        self.client.post("/api/settings", json=config())
        call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "find_nearby_places", "arguments": '{"query":"食堂","limit":2}'}}]}}]}
        answer = {"choices": [{"message": {"role": "assistant", "content": "离你最近的是……"}}]}
        with patch("src.llm.providers.post_json", side_effect=[call, answer]) as http:
            response = self.chat("附近哪里能吃饭？", location=at("library"))
        self.assertEqual(response.status_code, 200, response.json)
        sent = json.dumps(http.call_args_list[0], ensure_ascii=False)
        self.assertIn("get_user_location", sent)
        self.assertIn("已共享位置", sent)
        result = response.json["trace"][0]["result"]
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(response.json["map_targets"])

    def test_prompt_contains_location_summary(self):
        self.client.post("/api/settings", json=config())
        answer = {"choices": [{"message": {"role": "assistant", "content": "你在中部。"}}]}
        with patch("src.llm.providers.post_json", return_value=answer) as http:
            self.chat("我在哪", location=at("library"))
        sent = json.dumps(http.call_args_list[0], ensure_ascii=False)
        self.assertIn("逸夫图书馆约", sent)
        self.assertIn("不得声称看不到用户位置", sent)

    def test_location_failure_reason_reaches_tool(self):
        self.client.post("/api/settings", json=config())
        call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_user_location", "arguments": "{}"}}]}}]}
        answer = {"choices": [{"message": {"role": "assistant", "content": "请允许定位。"}}]}
        with patch("src.llm.providers.post_json", side_effect=[call, answer]) as http:
            response = self.chat("我在哪", location_status="denied")
        self.assertIn("权限被拒绝", json.dumps(http.call_args_list[0], ensure_ascii=False))
        self.assertIn("权限被拒绝", response.json["trace"][0]["result"]["reason"])

    def test_scope_banner_not_duplicated(self):
        from src.workbench.service import CHD_BANNER, strip_banner
        self.assertEqual(strip_banner(f"{CHD_BANNER}\n\n{CHD_BANNER}\n回答"), "回答")
        self.assertEqual(strip_banner("回答"), "回答")

    def test_model_tool_rejects_bad_integer(self):
        wb = self.app.extensions["workbench"]
        from src.navigation import LOCATION_SCHEMAS
        out = wb.execute("find_nearby_places", {"limit": 99}, LOCATION_SCHEMAS, None, False, {}, "附近", [], location=at("library"))
        self.assertFalse(out["ok"])
        out = wb.execute("find_nearby_places", {"category": "unknown"}, LOCATION_SCHEMAS, None, False, {}, "附近", [], location=at("library"))
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()

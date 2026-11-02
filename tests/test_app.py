import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["THOUGHTGARDEN_DB"] = _tmp.name

import app as app_module  # noqa: E402


class ThoughtGardenTest(unittest.TestCase):
    def setUp(self):
        app_module.init_db()
        self.client = app_module.app.test_client()
        with app_module.app.app_context():
            conn = app_module.get_db()
            conn.execute("DELETE FROM links")
            conn.execute("DELETE FROM nodes")
            conn.commit()

    # ---- helpers ----
    def add_node(self, title, kind="メモ", body=""):
        return self.client.post("/nodes", data={"title": title, "kind": kind, "body": body})

    def node_ids(self):
        with app_module.app.app_context():
            rows = app_module.get_db().execute("SELECT id FROM nodes ORDER BY id").fetchall()
            return [r["id"] for r in rows]

    def counts(self):
        with app_module.app.app_context():
            db = app_module.get_db()
            n = db.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
            l = db.execute("SELECT COUNT(*) AS n FROM links").fetchone()["n"]
            return n, l

    def add_pair_with_link(self, label="原因かも"):
        self.add_node("なぜ眠いのか", "疑問", "寝不足かも")
        self.add_node("寝る前のスマホ", "仮説")
        a, b = self.node_ids()
        res = self.client.post("/links", data={"from_id": a, "to_id": b, "label": label})
        return res, a, b

    # ---- tests ----
    def test_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

    def test_index_empty(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("Thought Garden", body)
        self.assertIn("まだノードが登録されていません", body)

    def test_add_node(self):
        res = self.add_node("なぜ眠いのか", "疑問", "寝不足かも")
        self.assertEqual(res.status_code, 302)
        nodes, links = self.counts()
        self.assertEqual(nodes, 1)
        self.assertEqual(links, 0)
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("なぜ眠いのか", body)
        self.assertIn("疑問", body)
        self.assertIn("寝不足かも", body)

    def test_add_node_all_kinds(self):
        for kind in ("疑問", "仮説", "メモ", "気づき"):
            self.add_node(f"{kind}のノード", kind)
        nodes, _ = self.counts()
        self.assertEqual(nodes, 4)

    def test_add_node_empty_title_rejected(self):
        self.add_node("   ")
        self.add_node("")
        nodes, _ = self.counts()
        self.assertEqual(nodes, 0)

    def test_add_node_invalid_kind_rejected(self):
        res = self.add_node("不正な種別のノード", "感想")
        self.assertEqual(res.status_code, 302)
        nodes, _ = self.counts()
        self.assertEqual(nodes, 0)
        body = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("不正な種別のノード", body)

    def test_delete_node(self):
        # フォームのプレースホルダ文言と重ならないタイトルを使う
        self.add_node("タロウの疑問", "疑問")
        self.add_node("ハナコの仮説", "仮説")
        a, _b = self.node_ids()
        res = self.client.post(f"/nodes/{a}/delete")
        self.assertEqual(res.status_code, 302)
        nodes, _ = self.counts()
        self.assertEqual(nodes, 1)
        body = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("タロウの疑問", body)
        self.assertIn("ハナコの仮説", body)

    def test_add_link_and_svg_on_page(self):
        res, a, b = self.add_pair_with_link("原因かも")
        self.assertEqual(res.status_code, 302)
        nodes, links = self.counts()
        self.assertEqual((nodes, links), (2, 1))
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("<svg", body)
        self.assertIn("なぜ眠いのか", body)
        self.assertIn("寝る前のスマホ", body)
        self.assertIn("原因かも", body)
        self.assertIn("<line", body)

    def test_build_svg_contains_titles_and_label(self):
        nodes = [
            {"id": 1, "title": "疑問ノード", "kind": "疑問", "body": ""},
            {"id": 2, "title": "仮説ノード", "kind": "仮説", "body": ""},
            {"id": 3, "title": "気づきノード", "kind": "気づき", "body": ""},
        ]
        links = [{"id": 1, "from_id": 1, "to_id": 2, "label": "検証する"}]
        svg = app_module.build_svg(nodes, links)
        self.assertTrue(svg.startswith("<svg"))
        for title in ("疑問ノード", "仮説ノード", "気づきノード"):
            self.assertIn(title, svg)
        self.assertIn("検証する", svg)
        self.assertEqual(svg.count("<circle"), 3)
        self.assertEqual(svg.count("<line"), 1)

    def test_build_svg_color_coded_by_kind(self):
        nodes = [
            {"id": 1, "title": "疑問タイトル", "kind": "疑問", "body": ""},
            {"id": 2, "title": "気づきタイトル", "kind": "気づき", "body": ""},
        ]
        svg = app_module.build_svg(nodes, [])
        self.assertIn(app_module.KIND_COLORS["疑問"][1], svg)
        self.assertIn(app_module.KIND_COLORS["気づき"][1], svg)

    def test_build_svg_single_node_centered(self):
        svg = app_module.build_svg([{"id": 1, "title": "ひとりノード", "kind": "メモ", "body": ""}], [])
        self.assertIn("ひとりノード", svg)
        self.assertNotIn("<line", svg)

    def test_delete_node_removes_links(self):
        self.add_node("タロウの疑問", "疑問")
        self.add_node("ハナコのメモ", "メモ")
        a, b = self.node_ids()
        self.client.post("/links", data={"from_id": a, "to_id": b, "label": "関連"})
        res = self.client.post(f"/nodes/{a}/delete")
        self.assertEqual(res.status_code, 302)
        nodes, links = self.counts()
        self.assertEqual(nodes, 1)
        self.assertEqual(links, 0)

    def test_delete_link(self):
        self.add_pair_with_link()
        with app_module.app.app_context():
            lid = app_module.get_db().execute("SELECT id FROM links").fetchone()["id"]
        res = self.client.post(f"/links/{lid}/delete")
        self.assertEqual(res.status_code, 302)
        nodes, links = self.counts()
        self.assertEqual((nodes, links), (2, 0))

    def test_link_to_self_rejected(self):
        self.add_node("ひとつのノード", "疑問")
        (only,) = self.node_ids()
        res = self.client.post("/links", data={"from_id": only, "to_id": only, "label": "自己"})
        self.assertEqual(res.status_code, 302)
        _, links = self.counts()
        self.assertEqual(links, 0)

    def test_duplicate_link_rejected(self):
        self.add_pair_with_link()
        a, b = self.node_ids()
        # 同じ向きの重複
        res = self.client.post("/links", data={"from_id": a, "to_id": b, "label": "二重"})
        self.assertEqual(res.status_code, 302)
        # 向きを逆にした重複も受け付けない
        res = self.client.post("/links", data={"from_id": b, "to_id": a, "label": "逆向き"})
        self.assertEqual(res.status_code, 302)
        _, links = self.counts()
        self.assertEqual(links, 1)

    def test_link_unknown_node_rejected(self):
        self.add_node("実在ノード", "メモ")
        (only,) = self.node_ids()
        res = self.client.post("/links", data={"from_id": only, "to_id": 9999, "label": "不明"})
        self.assertEqual(res.status_code, 400)
        _, links = self.counts()
        self.assertEqual(links, 0)

    def test_svg_escapes_user_input(self):
        evil = '<script>alert("x")</script>'
        nodes = [
            {"id": 1, "title": evil, "kind": "疑問", "body": ""},
            {"id": 2, "title": "相手ノード", "kind": "メモ", "body": ""},
        ]
        links = [{"id": 1, "from_id": 1, "to_id": 2, "label": "<b>関連</b>"}]
        svg = app_module.build_svg(nodes, links)
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)
        self.assertNotIn("<b>関連</b>", svg)
        self.assertIn("&lt;b&gt;関連&lt;/b&gt;", svg)

    def test_page_escapes_user_input(self):
        self.add_node("<img src=x onerror=alert(1)>", "メモ", "<b>太字</b>")
        body = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("<img src=x onerror=alert(1)>", body)
        self.assertIn("&lt;img", body)
        self.assertNotIn("<b>太字</b>", body)
        self.assertIn("&lt;b&gt;", body)


if __name__ == "__main__":
    unittest.main()

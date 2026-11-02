"""Thought Garden（thoughtgarden）

疑問・仮説・メモ・気づきといった「思考のノード」を記録し、ノード同士の
つながりを線で結んだグラフ（SVG）をサーバー側で生成して表示する
思考整理アプリ。単一ファイル Flask + SQLite。外部 JS ライブラリは使わない。
"""
import html
import math
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, abort, g, jsonify, redirect, render_template_string, request, url_for

app = Flask(__name__)
JST = ZoneInfo("Asia/Tokyo")
DB_PATH = os.environ.get("THOUGHTGARDEN_DB", os.path.join(os.path.dirname(__file__), "thoughtgarden.db"))

# ノードの種別と、グラフでの色分け（塗り／枠）
NODE_KINDS = ["疑問", "仮説", "メモ", "気づき"]
KIND_COLORS = {
    "疑問": ("#e8f0fe", "#4a6fa5"),
    "仮説": ("#e6f4ea", "#3d7a4d"),
    "メモ": ("#f1f3f4", "#80868b"),
    "気づき": ("#fef7e0", "#b06000"),
}

# SVG の描画パラメータ
SVG_SIZE = 640
SVG_CENTER = SVG_SIZE // 2
SVG_RADIUS = 215
NODE_RADIUS = 40


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'メモ',
            body TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
    db.commit()
    db.close()


def now_str():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def node_positions(nodes):
    """ノード id -> (x, y)。円周上に均等配置（1件だけなら中心）。"""
    n = len(nodes)
    positions = {}
    if n == 1:
        positions[nodes[0]["id"]] = (float(SVG_CENTER), float(SVG_CENTER))
        return positions
    for i, node in enumerate(nodes):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        x = SVG_CENTER + SVG_RADIUS * math.cos(angle)
        y = SVG_CENTER + SVG_RADIUS * math.sin(angle)
        positions[node["id"]] = (x, y)
    return positions


def build_svg(nodes, links):
    """思考グラフの SVG 文字列を生成する。タイトル・ラベルは HTML エスケープする。"""
    positions = node_positions(nodes)
    parts = [
        f'<svg viewBox="0 0 {SVG_SIZE} {SVG_SIZE}" width="100%" '
        f'style="max-width:{SVG_SIZE}px;background:#fffdf7;border:1px solid #ccc;border-radius:8px" '
        f'role="img" aria-label="思考グラフ">'
    ]
    # リンクの線とラベル
    for link in links:
        a = positions.get(link["from_id"])
        b = positions.get(link["to_id"])
        if a is None or b is None:
            continue
        parts.append(
            f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
            f'stroke="#aaa" stroke-width="2"/>'
        )
        if link["label"]:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            parts.append(
                f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle" font-size="13" '
                f'fill="#555" style="paint-order:stroke" stroke="#fffdf7" stroke-width="4">'
                f'{html.escape(link["label"])}</text>'
            )
    # ノード（種別で色分け）
    for node in nodes:
        x, y = positions[node["id"]]
        fill, stroke = KIND_COLORS.get(node["kind"], KIND_COLORS["メモ"])
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{NODE_RADIUS}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dominant-baseline="central" '
            f'font-size="13" fill="#222">{html.escape(node["title"])}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


INDEX = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thought Garden</title>
<style>body{font-family:sans-serif;max-width:760px;margin:2em auto;padding:0 1em}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.3em .5em}
.note{background:#f4f4f4;border:1px solid #ccc;padding:.8em;border-radius:6px}
.kind{border-radius:4px;padding:.1em .4em;font-size:.85em}</style>
</head><body>
<h1>Thought Garden</h1>
<p>疑問・仮説・メモ・気づきを「思考のノード」として記録し、ノード同士をつないで眺める思考整理アプリです。ノードを登録し、関連するノード同士をリンクで結ぶと、下のグラフに線が表示されます。</p>
<p class="note">データはこのアプリの SQLite に保存されます。Render 無料プランでは再デプロイ等でデータが消える場合があります。</p>

<h2>思考グラフ</h2>
{% if nodes %}
{{ svg|safe }}
<p>凡例：{% for k in kinds %}<span class="kind" style="background:{{ kind_colors[k][0] }};border:1px solid {{ kind_colors[k][1] }}">{{ k }}</span> {% endfor %}</p>
{% else %}
<p>まだノードが登録されていません。下のフォームから最初の疑問や仮説を追加してください。</p>
{% endif %}

<h2>ノードを追加</h2>
<form method="post" action="{{ url_for('add_node') }}">
  <label>タイトル <input type="text" name="title" required placeholder="例：なぜ早起きできないのか"></label><br>
  <label>種別
    <select name="kind">{% for k in kinds %}<option value="{{ k }}">{{ k }}</option>{% endfor %}</select></label><br>
  <label>本文 <input type="text" name="body" size="60" placeholder="任意（例：寝る前のスマホが原因かもしれない）"></label><br>
  <button type="submit">追加する</button>
</form>

<h2>ノード一覧（{{ nodes|length }}件）</h2>
<table>
<tr><th>タイトル</th><th>種別</th><th>本文</th><th>登録日時</th><th>操作</th></tr>
{% for n in nodes %}
<tr>
  <td>{{ n.title }}</td>
  <td>{{ n.kind }}</td>
  <td>{{ n.body }}</td>
  <td>{{ n.created_at }}</td>
  <td><form method="post" action="{{ url_for('delete_node', nid=n.id) }}" style="display:inline" onsubmit="return confirm('このノードを削除しますか？（リンクも一緒に消えます）')"><button>削除</button></form></td>
</tr>
{% else %}
<tr><td colspan="5">登録されたノードはまだありません</td></tr>
{% endfor %}
</table>

<h2>リンクを追加</h2>
{% if nodes|length >= 2 %}
<form method="post" action="{{ url_for('add_link') }}">
  <label>ノードA
    <select name="from_id">{% for n in nodes %}<option value="{{ n.id }}">{{ n.title }}</option>{% endfor %}</select></label>
  <label>ノードB
    <select name="to_id">{% for n in nodes %}<option value="{{ n.id }}">{{ n.title }}</option>{% endfor %}</select></label><br>
  <label>ラベル <input type="text" name="label" placeholder="任意（例：原因かも）"></label>
  <button type="submit">リンクを追加する</button>
</form>
<p>同じノード同士のリンクと、すでにある組み合わせのリンクは登録されません。</p>
{% else %}
<p>リンクを追加するにはノードが2件以上必要です。</p>
{% endif %}

<h2>リンク一覧（{{ links_view|length }}件）</h2>
<table>
<tr><th>ノードA</th><th>ラベル</th><th>ノードB</th><th>操作</th></tr>
{% for l in links_view %}
<tr>
  <td>{{ l.from_title }}</td>
  <td>{{ l.label }}</td>
  <td>{{ l.to_title }}</td>
  <td><form method="post" action="{{ url_for('delete_link', lid=l.id) }}" style="display:inline" onsubmit="return confirm('このリンクを削除しますか？')"><button>削除</button></form></td>
</tr>
{% else %}
<tr><td colspan="4">登録されたリンクはまだありません</td></tr>
{% endfor %}
</table>
</body></html>"""


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    db = get_db()
    nodes = db.execute("SELECT * FROM nodes ORDER BY id").fetchall()
    links = db.execute("SELECT * FROM links ORDER BY id").fetchall()
    titles = {n["id"]: n["title"] for n in nodes}
    links_view = [
        {
            "id": l["id"],
            "label": l["label"],
            "from_title": titles.get(l["from_id"], "（不明）"),
            "to_title": titles.get(l["to_id"], "（不明）"),
        }
        for l in links
    ]
    return render_template_string(
        INDEX,
        nodes=nodes,
        links_view=links_view,
        svg=build_svg(nodes, links),
        kinds=NODE_KINDS,
        kind_colors=KIND_COLORS,
    )


@app.route("/nodes", methods=["POST"])
def add_node():
    title = (request.form.get("title") or "").strip()
    kind = (request.form.get("kind") or "メモ").strip()
    if kind not in NODE_KINDS:
        kind = None  # 不正な種別は登録しない
    if title and kind:
        db = get_db()
        db.execute(
            "INSERT INTO nodes(title, kind, body, created_at) VALUES(?, ?, ?, ?)",
            (title, kind, (request.form.get("body") or "").strip(), now_str()),
        )
        db.commit()
    return redirect(url_for("index"))


@app.route("/nodes/<int:nid>/delete", methods=["POST"])
def delete_node(nid):
    db = get_db()
    db.execute("DELETE FROM links WHERE from_id = ? OR to_id = ?", (nid, nid))
    db.execute("DELETE FROM nodes WHERE id = ?", (nid,))
    db.commit()
    return redirect(url_for("index"))


@app.route("/links", methods=["POST"])
def add_link():
    try:
        from_id = int(request.form.get("from_id") or "")
        to_id = int(request.form.get("to_id") or "")
    except ValueError:
        return redirect(url_for("index"))
    if from_id == to_id:
        return redirect(url_for("index"))
    db = get_db()
    ids = {r["id"] for r in db.execute("SELECT id FROM nodes").fetchall()}
    if from_id not in ids or to_id not in ids:
        abort(400)
    # 重複リンク（向きを逆にしたものも含む）は受け付けない
    pair = (min(from_id, to_id), max(from_id, to_id))
    for row in db.execute("SELECT from_id, to_id FROM links").fetchall():
        if (min(row["from_id"], row["to_id"]), max(row["from_id"], row["to_id"])) == pair:
            return redirect(url_for("index"))
    label = (request.form.get("label") or "").strip()
    db.execute(
        "INSERT INTO links(from_id, to_id, label, created_at) VALUES(?, ?, ?, ?)",
        (from_id, to_id, label, now_str()),
    )
    db.commit()
    return redirect(url_for("index"))


@app.route("/links/<int:lid>/delete", methods=["POST"])
def delete_link(lid):
    db = get_db()
    db.execute("DELETE FROM links WHERE id = ?", (lid,))
    db.commit()
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))

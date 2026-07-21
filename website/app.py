"""
الموقع الإلكتروني لشبكة الامير التعليمية
Flask app — يعمل في thread منفصل بجانب البوت
"""
import os
import time
import logging
import requests as _req
from flask import Flask, render_template, jsonify, request, redirect, abort, url_for

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = "Mdry7bot"
NEW_DAYS     = 14   # عدد الأيام لاعتبار الملزمة "جديدة"

# ── Cache بسيط للـ thumbnails (bid -> (url|None, timestamp)) ──────
_thumb_cache: dict = {}
_THUMB_TTL = 3600  # ثانية (ساعة واحدة)


def _get_mongo():
    from bot.data_access import get_mongo_db
    return get_mongo_db()


def _col(name: str):
    return _get_mongo()[name]


# ─────────────────────────────────────────────────────────────────────
# مساعدات البيانات
# ─────────────────────────────────────────────────────────────────────

def _btn(bid: int):
    return _col("buttons").find_one({"id": bid, "deleted": {"$ne": 1}, "hidden": {"$ne": 1}})


def _children(pid):
    q = {"deleted": {"$ne": 1}, "hidden": {"$ne": 1}}
    if pid is None:
        q["parent_id"] = None
    else:
        q["parent_id"] = pid
    return list(_col("buttons").find(q).sort([("ord", 1), ("id", 1)]))


def _items(bid: int):
    return list(_col("content_items").find({"button_id": bid}).sort([("ord", 1), ("id", 1)]))


def _rating(bid: int) -> dict:
    """إجمالي التقييم للزر."""
    pipeline = [
        {"$match": {"button_id": bid}},
        {"$group": {"_id": None, "cnt": {"$sum": 1}, "avg": {"$avg": "$rating"}}}
    ]
    result = list(_col("button_ratings").aggregate(pipeline))
    if not result:
        return {"count": 0, "avg": 0.0, "stars": ""}
    r = result[0]
    avg = float(r.get("avg") or 0)
    # نجوم نصية
    full  = int(avg)
    half  = 1 if (avg - full) >= 0.5 else 0
    empty = 5 - full - half
    stars = "★" * full + ("½" if half else "") + "☆" * empty
    return {"count": r["cnt"], "avg": round(avg, 1), "stars": stars}


def _breadcrumb(bid: int) -> list:
    """مسار الزر من الجذر."""
    path = []
    current = bid
    while current is not None:
        doc = _col("buttons").find_one({"id": current})
        if not doc:
            break
        path.append({"id": doc["id"], "label": doc.get("label", "")})
        current = doc.get("parent_id")
    path.reverse()
    return path


def _is_new(btn: dict) -> bool:
    created_at = btn.get("created_at")
    if not created_at:
        return False
    return time.time() - created_at < NEW_DAYS * 86400


def _thumb_url(bid: int) -> str | None:
    """يجلب رابط الصورة الأولى لهذا الزر من Telegram API ويخزنه مؤقتاً."""
    now = time.time()
    if bid in _thumb_cache:
        url, ts = _thumb_cache[bid]
        if now - ts < _THUMB_TTL:
            return url

    # أول عنصر من نوع photo
    item = _col("content_items").find_one(
        {"button_id": bid, "type": "photo"},
        sort=[("ord", 1), ("id", 1)]
    )
    if not item or not item.get("file_id"):
        _thumb_cache[bid] = (None, now)
        return None

    try:
        resp = _req.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": item["file_id"]},
            timeout=6
        )
        data = resp.json()
        if data.get("ok"):
            fp = data["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}"
            _thumb_cache[bid] = (url, now)
            return url
    except Exception as e:
        logging.debug(f"[thumb] bid={bid} error={e}")

    _thumb_cache[bid] = (None, now)
    return None


def _enrich(btn: dict) -> dict:
    """يضيف التقييم والـ thumbnail وشارة الجديد للزر."""
    bid = btn["id"]
    return {
        **btn,
        "rating":   _rating(bid),
        "is_new":   _is_new(btn),
        "thumb_url": _thumb_url(bid),
        "click_count": btn.get("click_count", 0),
    }


def _search_content(q: str, limit: int = 50) -> list:
    """بحث نصي في أسماء الملازم (نوع content)."""
    docs = list(_col("buttons").find({
        "type": "content",
        "deleted": {"$ne": 1},
        "hidden": {"$ne": 1},
        "label": {"$regex": q, "$options": "i"}
    }).limit(limit))
    return [_enrich(d) for d in docs]


# ─────────────────────────────────────────────────────────────────────
# Flask app factory
# ─────────────────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("SESSION_SECRET", "alameer-secret")

    # ── الصفحة الرئيسية ──────────────────────────────────────────────
    @app.route("/")
    def index():
        categories = _children(None)
        return render_template("index.html",
            categories=categories,
            bot_username=BOT_USERNAME
        )

    # ── صفحة فئة / قسم ───────────────────────────────────────────────
    @app.route("/cat/<int:bid>")
    def category(bid: int):
        btn = _btn(bid)
        if not btn:
            abort(404)
        children  = _children(bid)
        menus     = [c for c in children if c.get("type") != "content"]
        contents  = [_enrich(c) for c in children if c.get("type") == "content"]
        breadcrumb = _breadcrumb(bid)
        return render_template("category.html",
            btn=btn,
            menus=menus,
            contents=contents,
            breadcrumb=breadcrumb,
            bot_username=BOT_USERNAME
        )

    # ── صفحة الملزمة ─────────────────────────────────────────────────
    @app.route("/note/<int:bid>")
    def note(bid: int):
        btn = _btn(bid)
        if not btn or btn.get("type") != "content":
            abort(404)
        items      = _items(bid)
        rating     = _rating(bid)
        breadcrumb = _breadcrumb(bid)
        thumb      = _thumb_url(bid)
        # نص المقدمة: أول عنصر نصي
        preview_text = next(
            (it.get("content", "") for it in items if it.get("type") == "text" and it.get("content")),
            ""
        )
        # كل الصور للمعاينة
        photos = [it for it in items if it.get("type") == "photo" and it.get("file_id")]
        return render_template("note.html",
            btn=btn,
            items=items,
            rating=rating,
            breadcrumb=breadcrumb,
            thumb=thumb,
            preview_text=preview_text,
            photos=photos,
            bot_username=BOT_USERNAME
        )

    # ── صفحة البحث ───────────────────────────────────────────────────
    @app.route("/search")
    def search():
        q       = request.args.get("q", "").strip()
        results = _search_content(q) if q else []
        return render_template("search.html",
            query=q,
            results=results,
            bot_username=BOT_USERNAME
        )

    # ── API بحث (JSON لـ live search) ────────────────────────────────
    @app.route("/api/search")
    def api_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify([])
        docs = list(_col("buttons").find({
            "type": "content",
            "deleted": {"$ne": 1},
            "hidden": {"$ne": 1},
            "label": {"$regex": q, "$options": "i"}
        }).limit(15))
        return jsonify([
            {"id": d["id"], "label": d.get("label", ""), "url": f"/note/{d['id']}"}
            for d in docs
        ])

    # ── Thumbnail proxy ───────────────────────────────────────────────
    @app.route("/thumb/<int:bid>")
    def thumb(bid: int):
        url = _thumb_url(bid)
        if url:
            return redirect(url)
        return redirect(url_for("static", filename="img/no-thumb.svg"))

    # ── صفحة 404 ─────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html", bot_username=BOT_USERNAME), 404

    return app

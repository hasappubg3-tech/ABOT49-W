"""
الموقع الإلكتروني لشبكة الامير التعليمية
Flask app — يعمل في process منفصل بجانب البوت
"""
import os
import time
import logging
import requests as _req
from flask import Flask, render_template, jsonify, request, redirect, abort, url_for, Response

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Mdry7bot")
SITE_NAME    = "شبكة الامير التعليمية"
NEW_DAYS     = 14   # عدد الأيام لاعتبار الملزمة "جديدة"

# ── Cache بسيط (file_id / bid -> (url|None, timestamp)) ──────────
_file_url_cache: dict = {}
_FILE_URL_TTL = 3600  # ساعة واحدة


def _get_mongo():
    from bot.data_access import get_mongo_db
    return get_mongo_db()


def _col(name: str):
    return _get_mongo()[name]


# ─────────────────────────────────────────────────────────────────────
# مساعدات Telegram API
# ─────────────────────────────────────────────────────────────────────

def _file_url(file_id: str) -> str | None:
    """يجلب رابط ملف من Telegram API ويخزنه مؤقتاً."""
    if not file_id or not BOT_TOKEN:
        return None
    now = time.time()
    cached = _file_url_cache.get(file_id)
    if cached:
        url, ts = cached
        if now - ts < _FILE_URL_TTL:
            return url

    try:
        resp = _req.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=6,
        )
        data = resp.json()
        if data.get("ok"):
            fp = data["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}"
            _file_url_cache[file_id] = (url, now)
            return url
    except Exception as e:
        logging.debug(f"[file_url] error={e}")

    _file_url_cache[file_id] = (None, now)
    return None


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
    pipeline = [
        {"$match": {"button_id": bid}},
        {"$group": {"_id": None, "cnt": {"$sum": 1}, "avg": {"$avg": "$rating"}}}
    ]
    result = list(_col("button_ratings").aggregate(pipeline))
    if not result:
        return {"count": 0, "avg": 0.0, "stars": ""}
    r = result[0]
    avg = float(r.get("avg") or 0)
    full  = int(avg)
    half  = 1 if (avg - full) >= 0.5 else 0
    empty = 5 - full - half
    stars = "★" * full + ("½" if half else "") + "☆" * empty
    return {"count": r["cnt"], "avg": round(avg, 1), "stars": stars}


def _breadcrumb(bid: int) -> list:
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
    """يجلب رابط الصورة الأولى لهذا الزر."""
    item = _col("content_items").find_one(
        {"button_id": bid, "type": "photo"},
        sort=[("ord", 1), ("id", 1)]
    )
    if not item or not item.get("file_id"):
        return None
    return _file_url(item["file_id"])


def _enrich(btn: dict) -> dict:
    bid = btn["id"]
    return {
        **btn,
        "rating":      _rating(bid),
        "is_new":      _is_new(btn),
        "thumb_url":   _thumb_url(bid),
        "click_count": btn.get("click_count", 0),
    }


def _search_content(q: str, limit: int = 50) -> list:
    docs = list(_col("buttons").find({
        "type": "content",
        "deleted": {"$ne": 1},
        "hidden":  {"$ne": 1},
        "label":   {"$regex": q, "$options": "i"}
    }).limit(limit))
    return [_enrich(d) for d in docs]


def _latest_notes(limit: int = 8) -> list:
    """أحدث الملازم المضافة."""
    docs = list(_col("buttons").find({
        "type":    "content",
        "deleted": {"$ne": 1},
        "hidden":  {"$ne": 1},
    }).sort("created_at", -1).limit(limit))
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
        latest     = _latest_notes(8)
        return render_template("index.html",
            categories=categories,
            latest=latest,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            og_title=SITE_NAME,
            og_description="مكتبة ملازم وكتب دراسية مجانية لجميع الصفوف",
            og_url="/",
            og_image="",
        )

    # ── صفحة فئة / قسم ───────────────────────────────────────────────
    @app.route("/cat/<int:bid>")
    def category(bid: int):
        btn = _btn(bid)
        if not btn:
            abort(404)
        children   = _children(bid)
        menus      = [c for c in children if c.get("type") != "content"]
        contents   = [_enrich(c) for c in children if c.get("type") == "content"]
        breadcrumb = _breadcrumb(bid)
        return render_template("category.html",
            btn=btn,
            menus=menus,
            contents=contents,
            breadcrumb=breadcrumb,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            og_title=f"{btn.get('label','')} — {SITE_NAME}",
            og_description=f"تصفح ملازم وكتب {btn.get('label','')}",
            og_url=f"/cat/{bid}",
            og_image="",
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
        # كل الصور — بـ file_id مستقل لكل صورة (إصلاح الغاليري)
        photos = [
            {"file_id": it.get("file_id")}
            for it in items
            if it.get("type") == "photo" and it.get("file_id")
        ]
        # ملفات PDF
        pdf_items = [it for it in items if it.get("type") == "document" and it.get("file_id")]
        pdf_url   = _file_url(pdf_items[0]["file_id"]) if pdf_items else None

        # رابط deep-link للبوت لفتح الملزمة مباشرة
        bot_deep_link = f"https://t.me/{BOT_USERNAME}?start=btn_{bid}"

        return render_template("note.html",
            btn=btn,
            items=items,
            rating=rating,
            breadcrumb=breadcrumb,
            thumb=thumb,
            preview_text=preview_text,
            photos=photos,
            pdf_url=pdf_url,
            bot_deep_link=bot_deep_link,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            og_title=f"{btn.get('label','')} — {SITE_NAME}",
            og_description=preview_text[:160] if preview_text else f"ملزمة {btn.get('label','')}",
            og_url=f"/note/{bid}",
            og_image=thumb or "",
        )

    # ── صفحة البحث ───────────────────────────────────────────────────
    @app.route("/search")
    def search():
        q       = request.args.get("q", "").strip()
        results = _search_content(q) if q else []
        return render_template("search.html",
            query=q,
            results=results,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            og_title=f"نتائج البحث: {q} — {SITE_NAME}" if q else f"بحث — {SITE_NAME}",
            og_description=f"نتائج البحث عن '{q}'" if q else "ابحث في مكتبة الامير",
            og_url=f"/search?q={q}",
            og_image="",
        )

    # ── API بحث (JSON لـ live search) ────────────────────────────────
    @app.route("/api/search")
    def api_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify([])
        docs = list(_col("buttons").find({
            "type":    "content",
            "deleted": {"$ne": 1},
            "hidden":  {"$ne": 1},
            "label":   {"$regex": q, "$options": "i"}
        }).limit(15))
        return jsonify([
            {"id": d["id"], "label": d.get("label", ""), "url": f"/note/{d['id']}"}
            for d in docs
        ])

    # ── Thumbnail بـ bid (للتوافق مع الكود القديم) ────────────────────
    @app.route("/thumb/<int:bid>")
    def thumb(bid: int):
        url = _thumb_url(bid)
        if url:
            return redirect(url)
        return redirect(url_for("static", filename="img/no-thumb.svg"))

    # ── File proxy بـ file_id (للغاليري والـ PDF) ────────────────────
    @app.route("/file/<path:file_id>")
    def file_proxy(file_id: str):
        url = _file_url(file_id)
        if not url:
            abort(404)
        # للـ PDF: نعيد البيانات مباشرة حتى يعمل الـ iframe في المتصفح
        try:
            r = _req.get(url, timeout=30, stream=True)
            content_type = r.headers.get("Content-Type", "application/octet-stream")
            is_pdf = "pdf" in content_type or file_id.lower().endswith(".pdf")
            if is_pdf:
                def generate():
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                resp_headers = {
                    "Content-Type": content_type,
                    "Content-Disposition": "inline",
                }
                if "Content-Length" in r.headers:
                    resp_headers["Content-Length"] = r.headers["Content-Length"]
                return Response(generate(), status=200, headers=resp_headers)
        except Exception as e:
            logging.debug(f"[file_proxy] fallback redirect: {e}")
        return redirect(url)

    # ── صفحة 404 ─────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html",
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            og_title=f"404 — {SITE_NAME}",
            og_description="الصفحة غير موجودة",
            og_url="/",
            og_image="",
        ), 404

    return app

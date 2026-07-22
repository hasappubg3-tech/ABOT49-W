"""
الموقع الإلكتروني لشبكة الامير التعليمية
Flask app — يعمل في process منفصل بجانب البوت
"""
import os
import re
import time
import logging
import requests as _req
from flask import Flask, render_template, jsonify, request, redirect, abort, url_for, Response

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Mdry7bot")
SITE_NAME    = "شبكة الامير التعليمية"

# ── إزالة الإيموجيات ────────────────────────────────────────────────
_EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U0000FE0F\U0000200D"
    "\U00002640-\U00002642"
    "\U00002600-\U00002B55"
    "]+",
    flags=re.UNICODE,
)

def strip_emoji(text) -> str:
    if not text:
        return ""
    return _EMOJI_RE.sub("", str(text)).strip()

NEW_DAYS        = 14      # عدد الأيام لاعتبار الملزمة "جديدة"
_PDF_THUMB_DIR  = "/tmp/pdf_thumbs"
_PDF_THUMB_TTL  = 86400   # يوم كامل

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


def _has_content_media(bid: int) -> bool:
    """هل يوجد صورة أو PDF لهذا الزر؟"""
    return bool(_col("content_items").find_one(
        {"button_id": bid, "type": {"$in": ["photo", "document"]}}
    ))


def _pdf_thumbnail(bid: int) -> bytes | None:
    """يولّد صورة JPEG من أول صفحة PDF ويخزّنها مؤقتاً."""
    os.makedirs(_PDF_THUMB_DIR, exist_ok=True)
    cache_path = f"{_PDF_THUMB_DIR}/{bid}.jpg"
    # استخدم الكاش إذا كان حديثاً
    try:
        if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < _PDF_THUMB_TTL:
            with open(cache_path, "rb") as f:
                return f.read()
    except OSError:
        pass
    # ابحث عن ملف PDF
    item = _col("content_items").find_one(
        {"button_id": bid, "type": "document"},
        sort=[("ord", 1), ("id", 1)]
    )
    if not item or not item.get("file_id"):
        return None
    pdf_url = _file_url(item["file_id"])
    if not pdf_url:
        return None
    try:
        resp = _req.get(pdf_url, timeout=30)
        if resp.status_code != 200:
            return None
        import fitz  # PyMuPDF
        doc = fitz.open(stream=resp.content, filetype="pdf")
        if doc.page_count == 0:
            return None
        page = doc[0]
        mat = fitz.Matrix(1.5, 1.5)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("jpeg")
        with open(cache_path, "wb") as f:
            f.write(img_bytes)
        return img_bytes
    except Exception as e:
        logging.warning(f"[pdf_thumb bid={bid}] {e}")
        return None


def _parse_content_lines(bid: int) -> dict:
    """
    يجلب أول عنصر ذي content في الملزمة ويستخرج منه:
      - title  : السطر الأول (نوع + مادة + جزء …)
      - teacher: ما بعد "للاستاذ"
      - year   : رقم رباعي من سطر السنة أو أي سطر
    """
    item = _col("content_items").find_one(
        {"button_id": bid, "content": {"$exists": True, "$ne": ""}},
        sort=[("ord", 1), ("id", 1)]
    )
    if not item:
        return {}

    raw   = item.get("content", "")
    lines = [re.sub(r'[⚜️🔸🔹|✨💫⭐★☆]+', '', l).strip() for l in raw.split("\n")]
    lines = [l.strip(" |–-") for l in lines if l.strip(" |–-")]

    title   = ""
    teacher = ""
    year    = ""

    for i, line in enumerate(lines):
        # السطر الأول كعنوان
        if not title and line:
            title = strip_emoji(line).strip()

        # اسم الأستاذ
        m_teacher = re.search(r'للاستاذ\s+(.+)', line)
        if m_teacher and not teacher:
            teacher = strip_emoji(m_teacher.group(1)).replace("|", "").strip()

        # السنة: من سطر "سنة الاصدار" أو أي رقم رباعي
        m_year = re.search(r'\b(20\d{2})\b', line)
        if m_year and not year:
            year = m_year.group(1)

    return {"title": title, "teacher": teacher, "year": year}


def _note_display_name(btn: dict) -> str:
    """يبني اسم الملزمة بصيغة: (السطر الأول من المحتوى) للاستاذ (الاسم) (السنة)"""
    bid  = btn["id"]
    info = _parse_content_lines(bid)

    title   = info.get("title", "")
    teacher = info.get("teacher", "")
    year    = info.get("year", "")

    # إذا لم يوجد عنوان من المحتوى، نرجع للتسمية الاحتياطية
    if not title:
        title = strip_emoji(btn.get("label", ""))

    parts = [title]
    if teacher:
        parts.append(f"للاستاذ {teacher}")
    if year and year not in title:
        parts.append(year)

    return " ".join(parts)


def _enrich(btn: dict) -> dict:
    bid = btn["id"]
    return {
        **btn,
        "rating":        _rating(bid),
        "is_new":        _is_new(btn),
        "thumb_url":     _has_content_media(bid),
        "click_count":   btn.get("click_count", 0),
        "display_label": _note_display_name(btn),
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
    app.jinja_env.filters["strip_emoji"] = strip_emoji

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
            title=SITE_NAME,
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
            title=f"{strip_emoji(btn.get('label',''))} — {SITE_NAME}",
            og_title=f"{strip_emoji(btn.get('label',''))} — {SITE_NAME}",
            og_description=f"تصفح ملازم وكتب {strip_emoji(btn.get('label',''))}",
            og_url=f"/cat/{bid}",
            og_image="",
        )

    # ── صفحة الملزمة ─────────────────────────────────────────────────
    @app.route("/note/<int:bid>")
    def note(bid: int):
        btn = _btn(bid)
        if not btn or btn.get("type") != "content":
            abort(404)
        items         = _items(bid)
        rating        = _rating(bid)
        breadcrumb    = _breadcrumb(bid)
        thumb         = _has_content_media(bid)
        display_label = _note_display_name(btn)

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
            display_label=display_label,
            preview_text=preview_text,
            photos=photos,
            pdf_url=pdf_url,
            bot_deep_link=bot_deep_link,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"{display_label} | {SITE_NAME}",
            og_title=f"{display_label} — {SITE_NAME}",
            og_description=preview_text[:160] if preview_text else f"ملزمة {display_label}",
            og_url=f"/note/{bid}",
            og_image="",
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
            title=f"نتائج البحث: {q} | {SITE_NAME}" if q else f"بحث | {SITE_NAME}",
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

    # ── Thumbnail: أول صفحة من PDF ────────────────────────────────────
    @app.route("/thumb/<int:bid>")
    def thumb(bid: int):
        data = _pdf_thumbnail(bid)
        if data:
            return Response(data, mimetype="image/jpeg",
                            headers={"Cache-Control": "max-age=3600"})
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
            title=f"404 — {SITE_NAME}",
            og_title=f"404 — {SITE_NAME}",
            og_description="الصفحة غير موجودة",
            og_url="/",
            og_image="",
        ), 404

    return app

from .shared import *

def detect_content(m):
    if m.photo:
        return "photo", m.caption, m.photo[-1].file_id
    if m.document:
        return "file", m.caption, m.document.file_id
    if m.video:
        return "video", m.caption, m.video.file_id
    if m.audio:
        return "audio", m.caption, m.audio.file_id
    if m.voice:
        return "audio", m.caption, m.voice.file_id
    if m.text:
        return "text", m.text, None
    return None, None, None

async def download_and_save(bot, file_id: str, file_type: str) -> str | None:
    try:
        import uuid
        ext_map = {"photo": "jpg", "video": "mp4", "audio": "mp3", "file": "bin"}
        ext = ext_map.get(file_type, "bin")
        filename = f"{MEDIA_DIR}/{file_type}_{uuid.uuid4().hex}.{ext}"
        tg_file = await bot.get_file(file_id)
        await tg_file.download_to_drive(filename)
        return filename
    except Exception as e:
        logging.warning(f"فشل تحميل الملف محلياً: {e}")
        return None

async def send_file_item(target, item, reply_markup=None, extra_caption=""):
    t = item["type"]
    fid = item.get("file_id")
    cap = item.get("content") or ""
    if extra_caption:
        cap = f"{cap}\n{extra_caption}" if cap else extra_caption
    lpath = item.get("local_path")
    iid = item.get("id")
    kwargs = {"caption": cap}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    async def _send_from_fid():
        if t == "photo":
            return await target.reply_photo(fid, **kwargs)
        elif t == "file":
            return await target.reply_document(fid, **kwargs)
        elif t == "video":
            return await target.reply_video(fid, **kwargs)
        elif t == "audio":
            return await target.reply_audio(fid, **kwargs)

    async def _send_from_local():
        if not lpath or not os.path.exists(lpath):
            return None
        with open(lpath, "rb") as f:
            if t == "photo":
                msg = await target.reply_photo(f, **kwargs)
            elif t == "file":
                msg = await target.reply_document(f, **kwargs)
            elif t == "video":
                msg = await target.reply_video(f, **kwargs)
            elif t == "audio":
                msg = await target.reply_audio(f, **kwargs)
            else:
                return None
        new_fid = None
        if msg:
            if t == "photo" and msg.photo:
                new_fid = msg.photo[-1].file_id
            elif t == "file" and msg.document:
                new_fid = msg.document.file_id
            elif t == "video" and msg.video:
                new_fid = msg.video.file_id
            elif t == "audio" and msg.audio:
                new_fid = msg.audio.file_id
            if new_fid and iid:
                upd_item_file_id(iid, new_fid)
        return msg

    if t == "text":
        return await target.reply_text(cap, **({"reply_markup": reply_markup} if reply_markup else {}))

    if fid:
        try:
            return await _send_from_fid()
        except Exception:
            pass
    return await _send_from_local()

async def clear_add_content_control(ctx, chat_id):
    msg_id = ctx.user_data.pop("add_content_control_msg_id", None)
    if msg_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

def kb_confirm_delete(bid):
    b = get_btn(bid)
    pid = b["parent_id"] if b else None
    back_cb = "m_r" if pid is None else f"m_{pid}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ موافق", callback_data=f"x_{bid}"),
        InlineKeyboardButton("❌ إلغاء", callback_data=back_cb),
    ]])


# ── مساعد اللوحة الثابتة ─────────────────────────────────────────
async def set_panel(ctx, chat_id, text, markup=None):
    msg = await ctx.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    ctx.user_data["panel_id"] = msg.message_id

# ── إذاعة رسالة لجميع المستخدمين ────────────────────────────────
async def do_broadcast(bot, from_chat_id: int, msg_id: int) -> tuple:
    """ينسخ الرسالة msg_id من from_chat_id إلى جميع المستخدمين. يُرجع (نجح, فشل)."""
    import asyncio
    user_ids = [r[0] for r in db().execute("SELECT user_id FROM user_stats").fetchall()]
    success = 0
    failed  = 0
    for uid in user_ids:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=from_chat_id, message_id=msg_id)
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)   # تجنب flood limits
    return success, failed

# ── إعادة إرسال نافذة التنبيه المعلقة (دون تحديث العداد) ─────────
async def resend_notif_gate(target, uid, bid):
    msg         = get_setting("notif_message", "🔔 يرجى الاشتراك في قناتنا!")
    chan        = get_setting("notif_channel", "").strip()
    ok_text     = get_setting("notif_ok_text",    "✅ نعم، اشتركت")
    cancel_text = get_setting("notif_cancel_text", "❌ لا، لاحقاً")

    rows = []
    if chan:
        url = chan if chan.startswith("http") else f"https://t.me/{chan.lstrip('@')}"
        rows.append([InlineKeyboardButton("📢 انضم للقناة الآن", url=url)])
    rows.append([
        InlineKeyboardButton(ok_text,     callback_data=f"notif_ok_{bid}"),
        InlineKeyboardButton(cancel_text, callback_data=f"notif_skip_{bid}"),
    ])
    markup = InlineKeyboardMarkup(rows)
    try:
        try:
            await target.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await target.reply_text(msg, reply_markup=markup)
    except Exception:
        pass

# ── عرض عناصر المحتوى للمستخدم ───────────────────────────────────
async def send_items(m, bid, uid=None, bot=None):
    if uid and not is_admin(uid):
        # النظام 1: هل هناك تنبيه منبثق معلق؟
        pending_bid = get_pending_notif(uid)
        if pending_bid:
            await resend_notif_gate(m, uid, pending_bid)
            return

        # فحص الاشتراك في القناة (مرة واحدة فقط)
        # True = مشترك | False = غير مشترك | None = تعذّر الفحص
        subscribed = await is_subscribed(bot, uid) if bot else None

        # النظام 1: تحديث العداد وفحص قبل إرسال المحتوى (فقط إذا تأكدنا أنه غير مشترك)
        if subscribed is False:
            inc_user_opens(uid)
            if should_notify(uid):
                await send_notif_gate(m, uid, bid)
                return  # حجب المحتوى عند ظهور التنبيه المنبثق

    items = get_items(bid)
    if not items:
        await m.reply_text("📭 لا يوجد محتوى بعد.")
        return
    if uid and not is_admin(uid):
        inc_click_count(bid, uid)
    b = get_btn(bid)
    no_cap = (b.get("no_caption", 0) or 0) if b else 0
    extra_cap = get_global_caption() if not no_cap else ""
    no_btn_cap = (b.get("no_btn_caption", 0) or 0) if b else 0
    cap_btns = get_caption_buttons() if not no_btn_cap else []
    link_markup = build_caption_btn_markup(cap_btns)
    unified = (b.get("unified_rating", 0) or 0) if b else 0
    ratings_hidden = get_user_ratings_hidden(uid) if uid and not is_admin(uid) else False
    for item in items:
        sent = await send_file_item(m, item, extra_caption=extra_cap, reply_markup=link_markup)
        if sent and uid and not is_admin(uid) and not unified and not ratings_hidden:
            await send_item_rating_message(m, item, uid=uid)

    # إرسال عبارة تحفيزية عشوائية بعد المحتوى (للمستخدمين فقط)
    if uid and not is_admin(uid):
        phrase = get_random_phrase()
        if phrase:
            try:
                await m.reply_text(phrase, message_effect_id="5046509860389126442")
            except Exception:
                await m.reply_text(phrase)

    # إرسال تقييم موحد واحد في الأسفل إذا كان توحيد التقييم مفعّلاً وغير مخفي
    if uid and not is_admin(uid) and unified and not ratings_hidden:
        await send_btn_unified_rating_message(m, bid, uid=uid)

# ── إرسال سؤال كويز للمستخدم ─────────────────────────────────────
async def send_quiz(m, bid, uid=None, bot=None):
    b = get_btn(bid)
    random_q = (b.get("random_quiz", 0) or 0) if b else 0
    if random_q and uid:
        question = get_next_random_question(bid, uid)
    else:
        questions = get_quiz_questions(bid)
        question = questions[0] if questions else None
    if not question:
        await m.reply_text("📭 لا يوجد أسئلة بعد.")
        return
    opts = get_quiz_options(question["id"])
    if len(opts) < 2:
        await m.reply_text("⚠️ السؤال غير مكتمل (يحتاج خيارين على الأقل).")
        return
    correct_idx = question.get("correct_option", 0)
    if correct_idx >= len(opts):
        correct_idx = 0
    explanation = question.get("explanation", "") or ""
    await m.reply_poll(
        question=question["question"],
        options=[opt["text"] for opt in opts],
        type="quiz",
        correct_option_id=correct_idx,
        explanation=explanation if explanation else None,
        is_anonymous=False,
    )
    if uid and random_q:
        log_question_sent(uid, question["id"])

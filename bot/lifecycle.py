from .shared import *

# ── إعداد البوت ──────────────────────────────────────────────────
async def post_init(app):
    sid = os.environ.get("SUPER_ADMIN_ID", "").strip()
    if sid.isdigit() and not is_admin(int(sid)):
        add_admin(int(sid)); logging.info(f"Super admin {sid} added.")
    if sid.isdigit():
        app.job_queue.run_repeating(_auto_backup_job, interval=86400, first=3600, name="auto_backup")
        logging.info("تم جدولة النسخ الاحتياطي التلقائي كل 24 ساعة.")
    _setup_pomodoro_feature()
    logging.info("تم إعداد ميزة البومودورو.")

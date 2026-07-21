"""
نقطة تشغيل موقع شبكة الامير التعليمية
يُشغَّل بشكل مستقل عبر: python run_website.py
"""
from website.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

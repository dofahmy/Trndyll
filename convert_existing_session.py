"""
حوّل ملف Telethon الحالي إلى TELEGRAM_STRING_SESSION.

ضع هذا الملف بجوار forwarder_ksa_session.session وشغّله على جهازك فقط.
لا ترفع ملف .session ولا القيمة الناتجة إلى GitHub.
"""

from pathlib import Path

from telethon.sessions import SQLiteSession, StringSession


session_file = Path(__file__).with_name("forwarder_ksa_session.session")
if not session_file.exists():
    raise SystemExit(
        "ملف forwarder_ksa_session.session غير موجود بجوار البرنامج."
    )

sqlite_session = SQLiteSession(str(session_file))
if not sqlite_session.auth_key:
    sqlite_session.close()
    raise SystemExit("ملف الـSession لا يحتوي على تسجيل دخول صالح.")

string_session = StringSession.save(sqlite_session)
sqlite_session.close()

print("\nانسخي السطر التالي كاملًا إلى Railway Variable باسم:")
print("TELEGRAM_STRING_SESSION")
print("\n" + string_session + "\n")
print("تنبيه: هذه القيمة سرية. لا ترسليها ولا تضعيها في GitHub.")

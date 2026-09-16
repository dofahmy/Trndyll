"""شغّلي هذا الملف مرة واحدة على جهازك، وليس على Railway."""

from getpass import getpass

from telethon.sync import TelegramClient
from telethon.sessions import StringSession


print("سيتم إنشاء Telegram String Session لحسابك.")
api_id = int(input("TELEGRAM_API_ID: ").strip())
api_hash = getpass("TELEGRAM_API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session = client.session.save()

print("\nانسخي السطر التالي بالكامل إلى Railway Variable باسم:")
print("TELEGRAM_STRING_SESSION")
print("\n" + session + "\n")
print("تنبيه: هذه القيمة سرية مثل كلمة المرور؛ لا ترسليها لأي شخص ولا تضعيها في GitHub.")

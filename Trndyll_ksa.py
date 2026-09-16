"""
Trendyol Telegram Forwarder — Saudi Arabia

يقرأ بوستات القنوات المصدر، يفك ty.gl، يغيّر رقم الأفلييت في حقول
adjust_adgroup وutm_campaign وlink_userID، ينشئ timestamp جديدًا، يختصر
الرابط بالدومين الخاص بنا، ثم يرسل البوست إلى قنواتنا.

التثبيت:
    pip install telethon requests

متغيرات Railway:
    TRENDYOL_AFFILIATE_ID=236364332
    SHORT_BASE_URL=https://trndyll.com
    SHORT_DB_PATH=/data/trendyol_links.db
    PORT=8080

للاحتفاظ بالروابط بعد كل Deploy، أضيفي Railway Volume على /data.
"""

import asyncio
import hashlib
import os
import re
import secrets
import sqlite3
import string
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from telethon import TelegramClient, events
from telethon.sessions import StringSession

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ======================== الإعدادات ========================
_API_ID_VALUE = os.getenv("TELEGRAM_API_ID", "").strip()
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
TELEGRAM_STRING_SESSION = os.getenv("TELEGRAM_STRING_SESSION", "").strip()
SESSION_FILE = os.getenv("TELEGRAM_SESSION_FILE", "forwarder_ksa_session")

if not _API_ID_VALUE or not API_HASH:
    raise RuntimeError(
        "أضيفي TELEGRAM_API_ID و TELEGRAM_API_HASH في Railway Variables"
    )
API_ID = int(_API_ID_VALUE)

# عدّلي القنوات هنا، أو ضعيها في Railway Variables مفصولة بفاصلة.
DEFAULT_SOURCE_CHANNELS = [
    "@OffersSaudiofficial",
]
DEFAULT_DESTINATION_CHANNELS = [
    "@KSABeso",
]


def _channel_list(env_name, default):
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SOURCE_CHANNELS = _channel_list("SOURCE_CHANNELS", DEFAULT_SOURCE_CHANNELS)
DESTINATION_CHANNELS = _channel_list(
    "DESTINATION_CHANNELS", DEFAULT_DESTINATION_CHANNELS
)
TRENDYOL_AFFILIATE_ID = os.getenv(
    "TRENDYOL_AFFILIATE_ID", "236364332"
).strip()
SHORT_BASE_URL = os.getenv(
    "SHORT_BASE_URL", "https://trndyll.com"
).strip().rstrip("/")
SHORT_DB_PATH = os.getenv("SHORT_DB_PATH", "trendyol_links.db").strip()
SHORT_CODE_LENGTH = int(os.getenv("SHORT_CODE_LENGTH", "11"))
WEB_PORT = int(os.getenv("PORT", "8080"))

if not TRENDYOL_AFFILIATE_ID.isdigit():
    raise ValueError("TRENDYOL_AFFILIATE_ID لازم يكون أرقام فقط")
if not SHORT_BASE_URL.startswith("https://"):
    raise ValueError("SHORT_BASE_URL لازم يبدأ بـ https://")

LINK_RE = re.compile(r'https?://[^\s\]\)\[\(< >"\'\uFFFC]+'.replace('< >', '<>'))
TRENDYOL_DOMAINS = ("ty.gl", "trendyol.sa", "trendyol.com")
_DB_LOCK = threading.Lock()
_TIMESTAMP_LOCK = threading.Lock()
_LAST_TIMESTAMP = 0
_PROCESSED = set()


# ======================== قاعدة الروابط ========================
def _db_connect():
    parent = os.path.dirname(os.path.abspath(SHORT_DB_PATH))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(SHORT_DB_PATH, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS short_links (
            code TEXT PRIMARY KEY,
            target_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            click_count INTEGER NOT NULL DEFAULT 0,
            last_clicked_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def initialize_database():
    with _DB_LOCK:
        conn = _db_connect()
        conn.close()


def save_short_link(code, target_url):
    created_at = datetime.now(timezone.utc).isoformat()
    with _DB_LOCK:
        conn = _db_connect()
        try:
            conn.execute(
                "INSERT INTO short_links(code, target_url, created_at) VALUES(?, ?, ?)",
                (code, target_url, created_at),
            )
            conn.commit()
        finally:
            conn.close()


def get_short_link(code, count_click=False):
    with _DB_LOCK:
        conn = _db_connect()
        try:
            row = conn.execute(
                "SELECT target_url FROM short_links WHERE code = ?", (code,)
            ).fetchone()
            if row and count_click:
                conn.execute(
                    """
                    UPDATE short_links
                    SET click_count = click_count + 1, last_clicked_at = ?
                    WHERE code = ?
                    """,
                    (datetime.now(timezone.utc).isoformat(), code),
                )
                conn.commit()
            return row[0] if row else None
        finally:
            conn.close()


# ======================== معالجة Trendyol ========================
def is_trendyol_url(url):
    try:
        host = (urlsplit(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in TRENDYOL_DOMAINS)
    except Exception:
        return False


def next_unique_timestamp():
    global _LAST_TIMESTAMP
    with _TIMESTAMP_LOCK:
        current = int(time.time())
        _LAST_TIMESTAMP = max(current, _LAST_TIMESTAMP + 1)
        return str(_LAST_TIMESTAMP)


def expand_trendyol_url(url):
    """يفك ty.gl ويتأكد أن الوجهة النهائية تابعة لـTrend­yol."""
    host = (urlsplit(url).hostname or "").lower()
    if host != "ty.gl" and not host.endswith(".ty.gl"):
        return url

    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(
                url,
                allow_redirects=True,
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/152 Safari/537.36"
                    ),
                    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
                },
            )
            response.raise_for_status()
            final_url = response.url
            final_host = (urlsplit(final_url).hostname or "").lower()
            if "trendyol." not in final_host:
                raise ValueError(
                    f"رابط ty.gl حوّل إلى دومين غير Trendyol: {final_host}"
                )
            return final_url
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1)
    raise RuntimeError(f"تعذر فك رابط ty.gl: {last_error}")


def retag_trendyol_url(url):
    """يغيّر حقول التتبع فقط ولا يلمس المنتج أو التاجر أو الحملة."""
    final_url = expand_trendyol_url(url)
    parts = urlsplit(final_url)
    host = (parts.hostname or "").lower()
    if "trendyol." not in host:
        raise ValueError("الرابط النهائي ليس رابط Trendyol")

    replacements = {
        "adjust_adgroup": TRENDYOL_AFFILIATE_ID,
        "utm_campaign": TRENDYOL_AFFILIATE_ID,
        "link_userID": TRENDYOL_AFFILIATE_ID,
        "timestamp": next_unique_timestamp(),
    }
    output_pairs = []
    seen = set()
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in replacements:
            if key not in seen:
                output_pairs.append((key, replacements[key]))
                seen.add(key)
        else:
            output_pairs.append((key, value))

    # نضمن وجود رقمنا في الأماكن الثلاثة حتى إن كان أحدها غير موجود بالمصدر.
    for key in ("adjust_adgroup", "utm_campaign", "link_userID", "timestamp"):
        if key not in seen:
            output_pairs.append((key, replacements[key]))

    return urlunsplit(
        (
            parts.scheme or "https",
            parts.netloc,
            parts.path,
            urlencode(output_pairs, doseq=True),
            parts.fragment,
        )
    )


def create_short_link(long_url):
    """يولّد رابطًا مختلفًا كل مرة، حتى للمنتج نفسه."""
    alphabet = string.ascii_letters + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(SHORT_CODE_LENGTH))
        try:
            save_short_link(code, long_url)
            return f"{SHORT_BASE_URL}/{code}"
        except sqlite3.IntegrityError:
            continue


def replace_trendyol_links(text):
    converted = 0
    errors = []

    def replace(match):
        nonlocal converted
        original_url = match.group(0)
        if not is_trendyol_url(original_url):
            return original_url
        try:
            long_url = retag_trendyol_url(original_url)
            short_url = create_short_link(long_url)
            converted += 1
            print(f"   ✅ رابط Trendyol الجديد: {short_url}")
            return short_url
        except Exception as exc:
            errors.append(str(exc))
            print(f"   ❌ فشل الرابط: {str(exc)[:180]}")
            return original_url

    new_text = LINK_RE.sub(replace, text or "")
    return new_text, converted, errors


# ======================== سيرفر الاختصار ========================
class ShortLinkHandler(BaseHTTPRequestHandler):
    def respond(self, include_body=True):
        path = urlsplit(self.path).path.strip("/")
        if path in ("", "health"):
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if include_body:
                self.wfile.write(body)
            return

        target_url = get_short_link(path, count_click=True)
        if not target_url:
            body = "الرابط غير موجود أو انتهت صلاحيته".encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if include_body:
                self.wfile.write(body)
            return

        self.send_response(302)
        self.send_header("Location", target_url)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.respond(include_body=True)

    def do_HEAD(self):
        self.respond(include_body=False)

    def log_message(self, _format, *_args):
        return


def start_short_link_server():
    server = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), ShortLinkHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="trendyol-short-link-server",
    )
    thread.start()
    print(f"🔗 خدمة الاختصار شغالة: {SHORT_BASE_URL} — port {WEB_PORT}")
    return server


# ======================== Telegram Forwarder ========================
if TELEGRAM_STRING_SESSION:
    client = TelegramClient(
        StringSession(TELEGRAM_STRING_SESSION), API_ID, API_HASH,
        connection_retries=None, retry_delay=5, auto_reconnect=True,
        request_retries=5, timeout=30,
    )
else:
    client = TelegramClient(
        SESSION_FILE, API_ID, API_HASH,
        connection_retries=None, retry_delay=5, auto_reconnect=True,
        request_retries=5, timeout=30,
    )


async def send_modified_post(channel, message, new_text):
    """يرسل نفس الميديا إن وجدت، وإلا يرسل النص فقط."""
    if message.media:
        try:
            await client.send_file(
                channel,
                message.media,
                caption=new_text[:1024],
                force_document=False,
            )
            return
        except Exception as exc:
            print(f"   ⚠️ تعذر إرسال الميديا مباشرة: {str(exc)[:100]}")
    await client.send_message(channel, new_text, link_preview=False)


async def handle_post(event):
    message = event.message
    text = message.message or ""

    fingerprint = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]
    message_key = (event.chat_id, message.id, fingerprint)
    if message_key in _PROCESSED:
        return
    _PROCESSED.add(message_key)
    if len(_PROCESSED) > 5000:
        _PROCESSED.pop()

    trendyol_urls = [url for url in LINK_RE.findall(text) if is_trendyol_url(url)]
    if not trendyol_urls:
        print("⏭️ البوست مفيهوش رابط Trendyol — اتخطّى")
        return

    try:
        chat = await event.get_chat()
        source_name = (
            f"@{chat.username}" if getattr(chat, "username", None)
            else str(event.chat_id)
        )
    except Exception:
        source_name = str(event.chat_id)

    print(f"\n📩 بوست Trendyol من {source_name} — {len(trendyol_urls)} رابط")
    new_text, converted, errors = replace_trendyol_links(text)

    # لا ننشر لو فشل أي رابط، حتى لا يخرج تاج شخص آخر.
    if errors or converted != len(trendyol_urls):
        print("   ⛔ لم يتم إرسال البوست لأن رابطًا واحدًا على الأقل فشل")
        return

    for channel in DESTINATION_CHANNELS:
        try:
            await send_modified_post(channel, message, new_text)
            print(f"   ✅ اتبعت على {channel}")
        except Exception as exc:
            print(f"   ❌ فشل الإرسال على {channel}: {str(exc)[:180]}")


async def run_forwarder():
    client.add_event_handler(handle_post, events.NewMessage(chats=SOURCE_CHANNELS))
    client.add_event_handler(handle_post, events.MessageEdited(chats=SOURCE_CHANNELS))
    if not TELEGRAM_STRING_SESSION:
        raise RuntimeError(
            "TELEGRAM_STRING_SESSION غير موجود في Railway Variables. "
            "شغّلي generate_telegram_session.py على جهازك وضعي الناتج كمتغير سرّي."
        )
    await client.start()
    print("=" * 58)
    print("🟠 Trendyol Forwarder شغال")
    print(f"رقم الأفلييت: {TRENDYOL_AFFILIATE_ID}")
    print(f"المصادر: {', '.join(SOURCE_CHANNELS)}")
    print(f"قنوات الإرسال: {', '.join(DESTINATION_CHANNELS)}")
    print("=" * 58)
    await client.run_until_disconnected()


if __name__ == "__main__":
    initialize_database()
    start_short_link_server()
    try:
        asyncio.run(run_forwarder())
    except KeyboardInterrupt:
        print("\n👋 تم إيقاف البرنامج")

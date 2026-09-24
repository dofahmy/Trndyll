#!/usr/bin/env python3
import asyncio
import os
import re
from telethon import TelegramClient
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import MessageEntityCode
from telethon.sessions import StringSession

_API_ID = os.getenv("TELEGRAM_API_ID", "").strip()
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
SESSION = os.getenv("TELEGRAM_STRING_SESSION", "").strip()
OUR_CODE = os.getenv("TRENDYOL_DISCOUNT_CODE", "OFFERZK").strip() or "OFFERZK"
PUBLIC_CODES = {"KSA15"}
CHANNELS = [
    x.strip()
    for x in os.getenv("DESTINATION_CHANNELS", "@KSAOfferzzz").split(",")
    if x.strip()
]
START_MESSAGE_ID = int(os.getenv("FIX_CODES_START_ID", "17116"))
DAYS_BACK = int(os.getenv("FIX_CODES_DAYS_BACK", "7"))

LINE_RE = re.compile(
    r"(?im)^(?P<prefix>\s*كود[^\S\r\n]+(?:ال)?خصم[^\S\r\n]*[:：][^\S\r\n]*)(?P<codes>[^\r\n]*)$"
)
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")

def fix_text(text):
    changed = False

    def repl(match):
        nonlocal changed
        tokens = TOKEN_RE.findall(match.group("codes") or "")
        uppers = {x.upper() for x in tokens}
        final = []
        if "KSA15" in uppers:
            final.append("KSA15")
        if OUR_CODE.upper() not in {x.upper() for x in final}:
            final.append(OUR_CODE)
        # Telegram may return the visible text without Markdown backticks even when
        # the codes are already monospace entities. Compare the actual code values,
        # not the literal backticks, to avoid MessageNotModified errors.
        current_codes = [x.upper() for x in tokens]
        wanted_codes = [x.upper() for x in final]
        if current_codes == wanted_codes:
            return match.group(0)

        new_line = match.group("prefix") + " ".join(final)
        changed = True
        return new_line

    new_text = LINE_RE.sub(repl, text or "")
    return new_text, changed

def utf16_len(value):
    return len(value.encode("utf-16-le")) // 2

def discount_entities(text):
    entities = []
    for match in LINE_RE.finditer(text or ""):
        code_area = match.group("codes") or ""
        area_start = match.start("codes")
        for token_match in TOKEN_RE.finditer(code_area):
            start = area_start + token_match.start()
            end = area_start + token_match.end()
            entities.append(
                MessageEntityCode(
                    offset=utf16_len(text[:start]),
                    length=utf16_len(text[start:end]),
                )
            )
    return entities

async def main():
    if not _API_ID or not API_HASH or not SESSION:
        raise RuntimeError(
            "لازم TELEGRAM_API_ID و TELEGRAM_API_HASH و TELEGRAM_STRING_SESSION يكونوا موجودين."
        )

    client = TelegramClient(StringSession(SESSION), int(_API_ID), API_HASH)
    await client.start()
    print(f"الكود الخاص بنا: {OUR_CODE}")
    print("الكود العام الوحيد المحفوظ: KSA15")

    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)

    total_checked = total_changed = total_failed = 0
    for channel in CHANNELS:
        print(
            f"\nفحص {channel} من الرسالة {START_MESSAGE_ID} "
            f"للخلف لمدة {DAYS_BACK} أيام ..."
        )
        # reverse=False هو ترتيب Telegram الطبيعي: من الأحدث إلى الأقدم.
        # max_id غير شامل، لذلك نضيف 1 لكي يبدأ من START_MESSAGE_ID نفسه.
        async for msg in client.iter_messages(
            channel,
            limit=None,
            max_id=START_MESSAGE_ID + 1,
            reverse=False,
        ):
            if msg.date and msg.date < cutoff:
                print(f"  وصلنا لحد الأسبوع: {msg.date.isoformat()} — توقف.")
                break
            total_checked += 1
            old_text = msg.message or ""
            if not old_text or not LINE_RE.search(old_text):
                continue

            new_text, changed = fix_text(old_text)
            if not changed:
                continue

            try:
                await client.edit_message(
                    channel,
                    msg.id,
                    new_text,
                    formatting_entities=discount_entities(new_text),
                    link_preview=False,
                )
                total_changed += 1
                print(f"  OK message_id={msg.id}")
                await asyncio.sleep(0.8)
            except MessageNotModifiedError:
                print(f"  SKIP message_id={msg.id} — الأكواد صحيحة بالفعل")
            except Exception as exc:
                total_failed += 1
                print(f"  FAIL message_id={msg.id}: {exc}")

    await client.disconnect()
    print(
        f"\nانتهى: checked={total_checked}, changed={total_changed}, failed={total_failed}"
    )

if __name__ == "__main__":
    asyncio.run(main())

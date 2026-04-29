#!/bin/bash
export $(grep -v '^#' /mnt/ssd/robopy_controller_host/.env | xargs -d '\n')
source ~/ros2_venv/bin/activate
python3 << 'EOF'
import asyncio, aioimaplib, os, email, re

def getenv(k, d=None):
    v = os.getenv(k, d)
    if v: return v.strip("'").strip('"')
    return v

def _parse_message(raw_bytes):
    msg = email.message_from_bytes(raw_bytes)
    subject = ""
    from email.header import decode_header as _rfc2047_decode
    for chunk, enc in _rfc2047_decode(msg.get("Subject", "") or ""):
        if isinstance(chunk, bytes):
            subject += chunk.decode(enc or "utf-8", errors="replace")
        else:
            subject += chunk
    sender = msg.get("From", "")
    date   = msg.get("Date", "")
    
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct   = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        raw_html = payload.decode(charset, errors="replace")
                        body = re.sub(r"<[^>]+>", " ", raw_html)
                        break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return { "from": sender, "subject": subject.strip(), "date": date, "body_snippet": body[:500] }

async def main():
    imap_server = getenv('IMAP_SERVER', 'imap.gmail.com')
    email_addr = getenv('EMAIL_ADDRESS')
    email_pass = getenv('EMAIL_PASSWORD')
    imap = aioimaplib.IMAP4_SSL(host=imap_server, port=993)
    await imap.wait_hello_from_server()
    await imap.login(email_addr, email_pass)
    await imap.select("INBOX")
    _, s = await imap.search("ALL")
    msg_ids = s[0].decode().split()[-3:]
    for msg_id in msg_ids:
        _, data = await imap.fetch(msg_id, "(RFC822)")
        raw_bytes = None
        for item in data:
            if isinstance(item, (bytes, bytearray)) and len(item) > 100:
                raw_bytes = bytes(item) if isinstance(item, bytearray) else item
                break
        if raw_bytes:
            parsed = _parse_message(raw_bytes)
            print(f"Parsed {msg_id}:")
            for k,v in parsed.items():
                print(f"  {k}: {repr(v)[:100]}")
        else:
            print(f"NO RAW BYTES FOR {msg_id}")
    await imap.logout()
asyncio.run(main())
EOF

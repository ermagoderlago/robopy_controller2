#!/bin/bash
export $(grep -v '^#' /mnt/ssd/robopy_controller_host/.env | xargs -d '\n')
source ~/ros2_venv/bin/activate
python3 << 'EOF'
import asyncio, aioimaplib, os

def getenv(k, d=None):
    v = os.getenv(k, d)
    if v:
        return v.strip("'").strip('"')
    return v

async def main():
    imap_server = getenv('IMAP_SERVER', 'imap.gmail.com')
    email_addr = getenv('EMAIL_ADDRESS')
    email_pass = getenv('EMAIL_PASSWORD')
    
    imap = aioimaplib.IMAP4_SSL(host=imap_server, port=993)
    await imap.wait_hello_from_server()
    await imap.login(email_addr, email_pass)
    await imap.select("INBOX")
    _, s = await imap.search("ALL")
    msg_ids = s[0].decode().split()
    if not msg_ids:
        print("NO EMAILS")
        return
    msg_id = msg_ids[-1]
    _, data = await imap.fetch(msg_id, "(RFC822)")
    print("data type:", type(data))
    for i, d in enumerate(data):
        print("item", i, type(d), len(d), repr(d[:100]))
    await imap.logout()
asyncio.run(main())
EOF

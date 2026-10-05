import os
import sys
import json
import urllib.parse
import urllib.request

token=os.getenv("BOT_TOKEN","").strip()
webhook_url=os.getenv("WEBHOOK_URL","").strip()
secret=os.getenv("WEBHOOK_SECRET","").strip()

if len(sys.argv)>1:
    webhook_url=sys.argv[1]

if not token or not webhook_url:
    print("Usage:")
    print("  Windows CMD: set BOT_TOKEN=NEW_TOKEN")
    print("  Windows CMD: set WEBHOOK_SECRET=your_secret")
    print("  python set_webhook.py https://YOUR-PROJECT.vercel.app/api/index")
    raise SystemExit(1)

payload={"url":webhook_url}
if secret:
    payload["secret_token"]=secret
payload["allowed_updates"]=json.dumps(["message","callback_query"])
payload["drop_pending_updates"]=True

data=urllib.parse.urlencode(payload).encode()
req=urllib.request.Request(
    f"https://api.telegram.org/bot{token}/setWebhook",
    data=data,
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as r:
    print(r.read().decode())

import json
import asyncio
import os
from http.server import BaseHTTPRequestHandler

from telegram import Update
from bot import build_application

async def process_update(data):
    app = build_application()
    await app.initialize()
    try:
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
    finally:
        await app.shutdown()

class handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"ok": True, "service": "Blackberry Telegram Bot"})

    def do_POST(self):
        secret = os.getenv("WEBHOOK_SECRET", "").strip()
        incoming = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret and incoming != secret:
            self._send(403, {"ok": False, "error": "forbidden"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            asyncio.run(process_update(data))
            self._send(200, {"ok": True})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

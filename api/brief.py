"""Vercel serverless function: POST {company, focus, password} -> {brief, sources}."""
import json, os, sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"error": "Bad request."})
        password = os.environ.get("APP_PASSWORD")
        if password and req.get("password") != password:
            return self._send(401, {"error": "Wrong password."})
        company = (req.get("company") or "").strip()[:120]
        focus = (req.get("focus") or "").strip()[:200]
        if not company:
            return self._send(400, {"error": "Type a company name."})
        try:
            from agent3_brief import run
            brief, sources = run(company, focus, log=lambda m: print(m, flush=True), save=False)
        except Exception as e:
            msg = str(e)
            hint = " Gemini is busy. Wait a minute and try again." if msg[:3] in ("429", "503") else ""
            return self._send(500, {"error": f"{type(e).__name__}: {msg[:300]}{hint}"})
        slim = [{"title": s["title"], "url": s["url"], "date": s["date"]} for s in sources]
        return self._send(200, {"company": company, "brief": brief, "sources": slim})

"""Web app for Agent 3 (Flask). Vercel runs this as one function.

Local test:  python app.py   then open http://127.0.0.1:5000
"""
import json
import os
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)


@app.get("/")
def page():
    resp = send_from_directory(os.path.join(HERE, "public"), "index.html")
    resp.headers["Cache-Control"] = "no-cache"  # always pick up a new deploy
    return resp


@app.post("/api/brief")
def brief():
    """Streams newline-delimited JSON events: stage, hits, read, token, done or error."""
    req = request.get_json(silent=True) or {}
    company = (req.get("company") or "").strip()[:120]
    focus = (req.get("focus") or "").strip()[:200]
    if not company:
        return jsonify(error="Type a company name."), 400
    detailed = bool(req.get("detailed"))

    def events():
        try:
            from agent3_brief import run_stream
            for ev in run_stream(company, focus, detailed):
                ev.pop("_full_sources", None)
                if ev["t"] == "stage":
                    print(ev["msg"], flush=True)
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:
            msg = str(e)
            hint = " Gemini is busy. Wait a minute and try again." if msg[:3] in ("429", "503") else ""
            yield json.dumps({"t": "error", "msg": f"{type(e).__name__}: {msg[:300]}{hint}"}) + "\n"

    return Response(stream_with_context(events()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    app.run(debug=False, threaded=True)

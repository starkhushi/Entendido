"""Web app for Agent 3 (Flask). Vercel runs this as one function.

Local test:  python app.py   then open http://127.0.0.1:5000
"""
import os
from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)


@app.get("/")
def page():
    return send_from_directory(os.path.join(HERE, "public"), "index.html")


@app.post("/api/brief")
def brief():
    req = request.get_json(silent=True) or {}
    company = (req.get("company") or "").strip()[:120]
    focus = (req.get("focus") or "").strip()[:200]
    if not company:
        return jsonify(error="Type a company name."), 400
    try:
        from agent3_brief import run
        text, sources = run(company, focus, log=lambda m: print(m, flush=True), save=False)
    except Exception as e:
        msg = str(e)
        hint = " Gemini is busy. Wait a minute and try again." if msg[:3] in ("429", "503") else ""
        return jsonify(error=f"{type(e).__name__}: {msg[:300]}{hint}"), 500
    slim = [{"title": s["title"], "url": s["url"], "date": s["date"]} for s in sources]
    return jsonify(company=company, brief=text, sources=slim)


if __name__ == "__main__":
    app.run(debug=False)

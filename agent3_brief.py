"""Agent 3 - Prospect brief writer.

Usage:  python agent3_brief.py "Company name" ["optional focus, e.g. the India unit"]

Free stack: Google via SearchApi.io (DuckDuckGo fallback) + page fetch + Gemini free tier for planning
and writing. Output: output/<company>.md with numbered sources.
"""
import os, re, sys, json, time, datetime
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]  # free-tier models that answered today
HERE = os.path.dirname(os.path.abspath(__file__))
CONTEXT = open(os.path.join(HERE, "entendido_context.md"), encoding="utf-8").read()
TODAY = datetime.date.today().isoformat()


def ask(prompt, json_mode=False, retries=4):
    """Call Gemini with model fallback and backoff on 429/503."""
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json" if json_mode else None)
    last = None
    for attempt in range(retries):
        for m in MODELS:
            try:
                r = client.models.generate_content(model=m, contents=prompt, config=cfg)
                return r.text
            except Exception as e:
                last = e
                if str(e)[:3] not in ("429", "503"):
                    raise
        time.sleep(8 * (attempt + 1))
    raise last


def plan_queries(company, focus):
    prompt = f"""You research companies for Entendido. Context:
{CONTEXT}

Company: {company}. Focus: {focus or 'none'}. Today: {TODAY}.
Write 5 short web search queries (3 to 6 words each, no years, no quotes) that will
surface leadership changes, M&A or integration, IPO, expansion, restructuring,
attrition, and who heads HR or the unit.
Return JSON: {{"queries": ["...", ...]}}"""
    queries = json.loads(ask(prompt, json_mode=True))["queries"][:5]
    # Fixed queries the human-written briefs always covered.
    queries += [
        f"{company} annual report 2025-26",
        f"{company} leadership development programme development centre employees",
        f"{company} complaint FIR court employees",
    ]
    news_queries = [company] + ([focus] if focus else []) + [f"{company} complaint case"]
    return queries, news_queries


SERP_KEY = os.environ.get("SERP_API_KEY")


def search_google(q, n, engine="google"):
    """SearchApi.io (Google web or Google News). Returns [] if no key or on error."""
    if not SERP_KEY:
        return []
    try:
        for attempt in range(2):
            try:
                r = requests.get("https://www.searchapi.io/api/v1/search", timeout=30,
                                 params={"q": q, "engine": engine, "api_key": SERP_KEY,
                                         "gl": "in", "hl": "en", "num": n})
                break
            except requests.exceptions.Timeout:
                if attempt:
                    raise
                time.sleep(3)
        r.raise_for_status()
        out = []
        for x in r.json().get("organic_results", []):
            link = x.get("link", "")
            if not link:
                continue
            # google.com/goto links are redirects; fetch_text follows them.
            out.append({"title": x.get("title", ""), "url": link,
                        "date": (x.get("date") or "")[:12], "snippet": x.get("snippet", "")})
        return out
    except Exception as e:
        print(f"  google search failed for {q!r}: {str(e)[:60]}")
        return []


def search_ddg(q, n):
    out = []
    try:
        with DDGS() as d:
            for fn in (d.news, d.text):
                try:
                    for r in fn(q, max_results=n):
                        out.append({"title": r.get("title", ""), "url": r.get("href") or r.get("url"),
                                    "date": (r.get("date") or "")[:10], "snippet": r.get("body", "")})
                except Exception as e:
                    if "No results" not in str(e):
                        print(f"  ddg search failed for {q!r}: {str(e)[:60]}")
    except Exception:
        pass
    return out


SKIP = ("linkedin.com/in/", "zoominfo.com", "signalhire.com", "ambitionbox.com",
        "x.com/", "twitter.com/", "facebook.com/")


def search(queries, news_queries, per_query=6, cap=30):
    """Run every query, then pick results round-robin so each query gets a share."""
    buckets = []
    for q in news_queries:            # recent dated coverage
        buckets.append(search_google(q, per_query, engine="google_news"))
        time.sleep(1)
    for q in queries:
        buckets.append(search_google(q, per_query) or search_ddg(q, per_query))
        time.sleep(1)
    seen, hits = set(), []
    for i in range(per_query):
        for b in buckets:
            if i < len(b):
                r = b[i]
                url = r.get("url", "")
                if not url or url in seen or any(s in url for s in SKIP):
                    continue
                seen.add(url)
                hits.append(r)
    return hits[:cap]


def fetch_text(url, limit=5000):
    """Return (final_url, text). Follows redirects, reads HTML or PDF."""
    try:
        r = requests.get(url, timeout=15, allow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9"})
        url = r.url
        if "pdf" in r.headers.get("content-type", "") or url.lower().endswith(".pdf"):
            # Annual reports are huge; read the first pages plus any page that
            # mentions leadership/people programmes, up to the limit.
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(r.content))
            keys = ("leadership", "development centre", "talent", "attrition", "people")
            chunks = []
            for i, page in enumerate(reader.pages[:120]):
                t = page.extract_text() or ""
                if i < 3 or any(k in t.lower() for k in keys):
                    chunks.append(re.sub(r"\s+", " ", t))
                if sum(len(c) for c in chunks) > limit * 3:
                    break
            return url, " ".join(chunks)[: limit * 3]
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        return url, text[:limit]
    except Exception:
        return url, ""


def write_brief(company, focus, sources):
    src_block = "\n\n".join(
        f"[{i}] {s['title']} ({s['date'] or 'n.d.'}) {s['url']}\n{s['snippet']}\n{s['text']}"
        for i, s in enumerate(sources, 1))
    prompt = f"""You write one-page prospect briefs for Entendido. Context about us:
{CONTEXT}

Today is {TODAY}. Company: {company}. Focus: {focus or 'none'}.

Use ONLY the numbered sources below. Every factual statement carries a citation like [3].
If something is unknown, say "not found in sources" rather than guessing.
Write in Entendido's voice: plain, specific, behavioural, no sales fluff.

Produce Markdown with EXACTLY these sections, matching our Emcure/Gennova sample:
# Prospect Brief: {company}
*Sector, one line. Created {TODAY}.*
## About This Research
(two lines: we look for a big job to do + a leadership group that changed shape; this does NOT establish a problem)
## About The Prospect
## Event Timeline
A table: | Event | When | Potential implications (derived only) |
## Points To Remember While Pitching
(fit rules: who to sell to, size, procurement risk, "we already do this" risk and our counter)
## What To Establish Early
(numbered, concrete unknowns)
## Who To Contact
(named people with role and why; flag who to avoid, e.g. group HR; mark unverified names)
## Potential Pitch / Conversation Opener
(a 3-sentence script asking for 20 minutes)
## Question Bank
(6 questions)
## Signals To Monitor
Two lists: pursue further / abandon
## Things To Know But Not Mention
(legal, sensitive; or "none found")
## Estimated Timing
(reach out, proposal, approvals, earliest delivery)
## Fit Score
One line: High / Medium / Low with a one-sentence reason.
## Sources
Numbered list, title and URL, only the ones you cited.

SOURCES:
{src_block}"""
    return ask(prompt)


def run(company, focus="", log=print):
    """Research a company and return (brief_markdown, sources). Also saves to output/."""
    log(f"Planning searches for {company}")
    queries, news_queries = plan_queries(company, focus)
    log(f"Searching Google with {len(queries) + len(news_queries)} queries")
    hits = search(queries, news_queries)
    log(f"Reading {len(hits)} pages")
    sources, seen_final = [], set()
    for h in hits:
        h["url"], h["text"] = fetch_text(h["url"])
        if h["url"] in seen_final or any(s in h["url"] for s in SKIP):
            continue
        seen_final.add(h["url"])
        if h["text"] or h["snippet"]:
            sources.append(h)
    log(f"Writing brief from {len(sources)} sources")
    brief = write_brief(company, focus, sources)
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
    path = os.path.join(HERE, "output", f"{slug}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(brief)
    with open(os.path.join(HERE, "output", f"{slug}.sources.json"), "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=1, ensure_ascii=False)
    log(f"Saved {path}")
    return brief, sources


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")


if __name__ == "__main__":
    main()

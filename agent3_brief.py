"""Agent 3 - Prospect brief writer.

Usage:  python agent3_brief.py "Company name" ["optional focus, e.g. the India unit"]

Free stack: Google via SearchApi.io (DuckDuckGo fallback) + page fetch + Gemini free tier for planning
and writing. Output: output/<company>.md with numbered sources.
"""
import os, re, sys, json, time, datetime
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, wait
from bs4 import BeautifulSoup
from ddgs import DDGS
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings from annual-report PDFs
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
FAST = ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash"]  # ~6s; retry lite once
DETAILED = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]  # about 40 seconds, richer
MODELS = FAST
HERE = os.path.dirname(os.path.abspath(__file__))
CONTEXT = open(os.path.join(HERE, "entendido_context.md"), encoding="utf-8").read()
TODAY = datetime.date.today().isoformat()


def ask(prompt, json_mode=False, retries=4, models=None):
    """Call Gemini with model fallback and backoff on 429/503."""
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json" if json_mode else None)
    last, start = None, time.time()
    for attempt in range(retries):
        if attempt and time.time() - start > 90:
            break
        for m in (models or MODELS):
            try:
                r = client.models.generate_content(model=m, contents=prompt, config=cfg)
                return r.text
            except Exception as e:
                last = e
                if str(e)[:3] not in ("429", "503"):
                    raise
        time.sleep(8 * (attempt + 1))
    raise last


def ask_stream(prompt, models=None, retries=3):
    """Yield Gemini output in pieces as it is written. Falls back between models
    on 429/503, but only before the first piece arrives."""
    last, start = None, time.time()
    for attempt in range(retries):
        if attempt and time.time() - start > 90:
            break
        for m in (models or MODELS):
            got = False
            try:
                # 3.5 Flash "thinks" for over a minute by default; low keeps it near 10s.
                cfg = (types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="low"))
                       if "3.5" in m else None)
                for chunk in client.models.generate_content_stream(model=m, contents=prompt, config=cfg):
                    if chunk.text:
                        got = True
                        yield chunk.text
                return
            except Exception as e:
                last = e
                if got or str(e)[:3] not in ("429", "503"):
                    raise
                time.sleep(1)
        time.sleep(8 * (attempt + 1))
    raise last


def plan_queries(company, focus):
    """Fixed templates. No Gemini call, so this is instant."""
    queries = [
        f"{company} acquisition merger integration",
        f"{company} CEO appointed leadership change",
        f"{company} CHRO head of HR",
        f"{company} expansion new plant market entry",
        f"{company} restructuring attrition layoffs",
        f"{company} annual report 2025-26",
        f"{company} leadership development programme employees",
        f"{company} learning and development academy training initiative",
    ]
    if focus:
        queries.insert(0, f"{company} {focus}")
    news_queries = ([company] + ([f"{company} {focus}"] if focus else [])
                    + [f"{company} appoints CEO head", f"{company} complaint case"])
    return queries, news_queries


SERP_KEY = os.environ.get("SERP_API_KEY")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY")


def search_tavily(q, n, news=False):
    """Tavily free plan: 1,000 searches a month. Returns [] if no key or on error."""
    if not TAVILY_KEY:
        return []
    try:
        body = {"query": q, "max_results": n, "search_depth": "basic",
                "topic": "news" if news else "general"}
        if news:
            body["days"] = 365
        else:
            body["country"] = "india"
        r = requests.post("https://api.tavily.com/search", json=body, timeout=20,
                          headers={"Authorization": f"Bearer {TAVILY_KEY}"})
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "date": (x.get("published_date") or "")[:16], "snippet": x.get("content", "")}
                for x in r.json().get("results", []) if x.get("url")]
    except Exception as e:
        print(f"  tavily search failed for {q!r}: {str(e)[:60]}")
        return []


def search_google(q, n, engine="google"):
    """SearchApi.io (Google web or Google News). Returns [] if no key or on error."""
    global SERP_KEY
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
        if "429" in str(e):  # out of credits: stop trying SearchApi for this run
            SERP_KEY = None
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


def parallel(fn, items, workers, seconds, default):
    """Run fn over items in parallel. Anything not done within `seconds` gets `default`."""
    pool = ThreadPoolExecutor(max_workers=workers)
    futs = [pool.submit(fn, x) for x in items]
    wait(futs, timeout=seconds)
    pool.shutdown(wait=False, cancel_futures=True)
    out = []
    for f, x in zip(futs, items):
        try:
            out.append(f.result() if f.done() else default(x))
        except Exception:
            out.append(default(x))
    return out


def search(queries, news_queries, per_query=6, cap=30):
    """Run every query in parallel, then pick results round-robin so each query gets a share."""
    jobs = [(q, "google_news") for q in news_queries] + [(q, "google") for q in queries]

    def one(job):
        q, engine = job
        news = engine == "google_news"
        return (search_tavily(q, per_query, news=news)
                or search_google(q, per_query, engine=engine)
                or ([] if news else search_ddg(q, per_query)))

    buckets = parallel(one, jobs, workers=8, seconds=25, default=lambda j: [])
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


def parse_date(d):
    """Turn the many date formats search engines and pages use into YYYY-MM-DD, or ''."""
    from email.utils import parsedate_to_datetime
    d = (d or "").strip()
    if not d:
        return ""
    tries = (lambda x: parsedate_to_datetime(x),
             lambda x: datetime.datetime.strptime(x[:16].strip(), "%a, %d %b %Y"),
             lambda x: datetime.datetime.fromisoformat(x[:10]),
             lambda x: datetime.datetime.strptime(x[:12].strip(), "%b %d, %Y"),
             lambda x: datetime.datetime.strptime(x.strip(), "%d %b %Y"),
             lambda x: datetime.datetime.strptime(x.strip(), "%B %d, %Y"))
    for t in tries:
        try:
            when = t(d)
            if 2000 <= when.year <= datetime.date.today().year + 1:
                return when.strftime("%Y-%m-%d")
        except Exception:
            pass
    m = re.search(r"(\d+)\s+(day|week|month|year)s?\s+ago", d)
    if m:
        days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2)]
        return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return ""


def page_date(soup):
    """Publish date from a page's own metadata, so undated search results get one."""
    for attrs in ({"property": "article:published_time"}, {"property": "og:published_time"},
                  {"name": "publish-date"}, {"name": "pubdate"}, {"itemprop": "datePublished"},
                  {"name": "date"}, {"property": "article:modified_time"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and parse_date(tag.get("content")):
            return parse_date(tag.get("content"))
    for script in soup.find_all("script", type="application/ld+json"):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', script.string or "")
        if m and parse_date(m.group(1)):
            return parse_date(m.group(1))
    t = soup.find("time", attrs={"datetime": True})
    return parse_date(t["datetime"]) if t else ""


def fetch_text(url, limit=5000):
    """Return (final_url, text, page_date). Follows redirects, reads HTML or PDF."""
    try:
        r = requests.get(url, timeout=6, allow_redirects=True, headers={
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
            return url, " ".join(chunks)[: limit * 3], ""
        soup = BeautifulSoup(r.text, "html.parser")
        published = page_date(soup)
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        return url, text[:limit], published
    except Exception:
        return url, "", ""


FIT_WEIGHTS = [("Evidence of need", 30), ("Capability fit", 25), ("Decision-maker access", 15),
               ("Urgency", 15), ("Commercial feasibility", 15)]


def score_fit(fit):
    """Attach weights, compute the weighted total out of 5 and the High/Medium/Low label."""
    by_name = {str(f.get("criterion", "")).lower(): f for f in fit if isinstance(f, dict)}
    rows = []
    for i, (name, w) in enumerate(FIT_WEIGHTS):
        f = by_name.get(name.lower()) or (fit[i] if i < len(fit) and isinstance(fit[i], dict) else {})
        try:
            sc = max(0, min(5, int(round(float(f.get("score") or 0)))))
        except Exception:
            sc = 0
        rows.append({"criterion": name, "weight": w, "score": sc,
                     "why": f.get("why", ""), "cite": f.get("cite", [])})
    raw = sum(r["score"] * r["weight"] for r in rows) / 100
    label = "High" if raw >= 3.5 else "Medium" if raw >= 2.5 else "Low"  # label from the unrounded score
    return rows, round(raw, 2), label


def fit_section(viz, reason):
    """Markdown for the Fit Score section, from the computed score."""
    cite = lambda c: f" [{', '.join(map(str, c))}]" if isinstance(c, list) and c else ""
    lines = [f"**{viz['fit_label']}: {viz['fit_total']} out of 5** (weighted)", "",
             "| Factor | Weight | Score | Evidence |", "| :--- | :--- | :--- | :--- |"]
    lines += [f"| {r['criterion']} | {r['weight']}% | {r['score']}/5 | {r['why']}{cite(r['cite'])} |"
              for r in viz["fit"]]
    if reason:
        lines += ["", reason]
    return "\n".join(lines)


def split_viz(text):
    """Pull the trailing ```json chart block off the brief. Returns (markdown, dict)."""
    m = re.search(r"```json\s*(\{.*\})\s*```\s*$", text.strip(), re.S)
    if not m:
        return text.strip(), {}
    try:
        viz = json.loads(m.group(1))
    except Exception:
        viz = {}
    viz = viz if isinstance(viz, dict) else {}
    if viz.get("fit"):
        viz["fit"], viz["fit_total"], viz["fit_label"] = score_fit(viz["fit"])
    # A trend chart must compare like with like: all years or all quarters, 2+ points.
    t = viz.get("trend") or {}
    pts = [p for p in t.get("points", []) if isinstance(p.get("y"), (int, float))]
    kinds = {bool(re.search(r"Q[1-4]|H[12]", str(p.get("x")))) for p in pts}
    viz["trend"] = dict(t, points=pts) if len(pts) >= 2 and len(kinds) == 1 else None
    brief = text.strip()[:m.start()].rstrip()
    if viz.get("fit"):
        # Replace whatever the model wrote under Fit Score with the computed table,
        # keeping its one-sentence reason.
        fm = re.search(r"(## Fit Score\s*\n)(.*?)(?=\n## |\Z)", brief, re.S)
        if fm:
            reason = " ".join(l.strip() for l in fm.group(2).strip().splitlines()
                              if l.strip() and not l.strip().startswith("|"))
            # Drop any score or label the model wrote, e.g. "**3.2 (Medium)**." or "High:".
            reason = re.sub(r"^\W*(\d(\.\d+)?\s*(/\s*5|out of 5)?\s*)?\(?\s*(High|Medium|Low)?\s*\)?\W*(fit)?[\s:.\-]*",
                            "", reason, flags=re.I) if re.match(r"^\W*(\d|High|Medium|Low)", reason, re.I) else reason
            brief = brief[:fm.start()] + fm.group(1) + fit_section(viz, reason) + "\n" + brief[fm.end():]
    return brief, viz


def coverage(sources):
    """Count dated sources per month."""
    counts = {}
    for x in sources:
        d = parse_date(x.get("date"))
        if d:
            counts[d[:7]] = counts.get(d[:7], 0) + 1
    if not counts:
        return []
    # Fill empty months so the time axis is even. Show at most the last 12.
    y, m = map(int, max(counts).split("-"))
    months = []
    for _ in range(12):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months = [k for k in reversed(months) if k >= min(counts)]
    return [{"month": k, "count": counts.get(k, 0)} for k in months]


TYPE_NAMES = {"deal": "Deal / M&A", "leadership": "Leadership", "growth": "Growth",
              "legal": "Legal / sensitive"}


def glance_md(viz):
    """The chart data as Markdown, so copied or downloaded briefs keep the analysis."""
    cite = lambda c: f" [{', '.join(map(str, c))}]" if isinstance(c, list) and c else ""
    out = ["## At A Glance"]
    kn = viz.get("key_numbers") or []
    if kn:
        out += ["", "| Key number | Value | Note |", "| :--- | :--- | :--- |"]
        out += [f"| {k.get('label', '')} | **{k.get('value', '')}** | {k.get('note', '')}{cite(k.get('cite'))} |"
                for k in kn[:4]]
    fit = viz.get("fit") or []
    if fit:
        out += ["", f"**Fit: {viz.get('fit_label', '')}, {viz.get('fit_total', '')} out of 5, weighted** "
                    "(the agent's judgement, not facts)", "",
                "| Factor | Weight | Score |", "| :--- | :--- | :--- |"]
        out += [f"| {f['criterion']} | {f['weight']}% | {'█' * f['score']}{'░' * (5 - f['score'])} {f['score']}/5 |"
                for f in fit]
    tl = sorted(viz.get("timeline") or [], key=lambda e: str(e.get("date")))
    if tl:
        out += ["", "| When | Event | Type |", "| :--- | :--- | :--- |"]
        out += [f"| {e.get('date', '')} | {e.get('event', '')}{cite(e.get('cite'))} | "
                f"{TYPE_NAMES.get(e.get('type'), 'Growth')} |" for e in tl]
    cov = [c for c in viz.get("coverage") or [] if c.get("count")]
    if len(cov) >= 2:
        out += ["", "News coverage by month (dated sources read): "
                + ", ".join(f"{c['month']}: {c['count']}" for c in cov)]
    t = viz.get("trend")
    if t:
        out += ["", f"{t.get('label', 'Trend')}{cite(t.get('cite'))}: "
                + ", ".join(f"{p['x']} {p['y']:,}" for p in t["points"])]
    return "\n".join(out) if len(out) > 1 else ""


def with_glance(brief, viz):
    """Insert the At A Glance section right after the title and subtitle."""
    g = glance_md(viz)
    if not g:
        return brief
    parts = brief.split("\n## ", 1)
    return parts[0].rstrip() + "\n\n" + g + ("\n\n## " + parts[1] if len(parts) > 1 else "")


def write_brief(company, focus, sources, detailed=False, prompt_only=False):
    src_block = "\n\n".join(
        f"[{i}] {s['title']} (published {s['date'] or 'date unknown'}) {s['url']}\n{s['snippet']}\n{s['text']}"
        for i, s in enumerate(sources, 1))
    prompt = f"""You write one-page prospect briefs for Entendido. Context about us:
{CONTEXT}

Today is {TODAY}. Company: {company}. Focus: {focus or 'none'}.

Use ONLY the numbered sources below. Every factual statement carries a citation like [3].
If something is unknown, say "not found in sources" rather than guessing.
Write in Entendido's voice: plain, specific, behavioural, no sales fluff.

Sources are ordered newest first and show their publish date. When sources disagree
(a person's title, who holds a role, a number), trust the most recent dated source and
write the role with its date, e.g. "CEO, House of Brands (as of Sep 2026) [4]". Never
take a current title from an undated source when a dated one exists. If a conflict
cannot be settled by date, say so and add it to What To Establish Early.

Write for someone designing a proposal: the brief must show which of our offerings
fits best, how to tailor it, and what to explore on the first call. Use the offering
table in the context. Pick the most specific development, not just the biggest one,
and do not default to "leadership integration" unless the evidence points there.
Before writing, scan every source for distinct developments, including business-model
changes (franchise or partner model, offline centres, new channels, new categories,
pricing or product shifts), not only deals and appointments. A business-model change
usually points to a more specific capability need than an acquisition does. Put every
distinct development in the Event Timeline, and take each row of Where Entendido Could
Fit from a different kind of development, with a different need and offering.

Produce Markdown with EXACTLY these sections, matching our Emcure/Gennova sample:
# Prospect Brief: {company}
*<the company's sector in a few words>. Created {TODAY}.*
## About This Research
(two lines: we look for a big job to do + a leadership group that changed shape; this does NOT establish a problem)
## About The Prospect
## Existing L&D And Internal Capabilities
(their own academies, leadership programmes, development centres, coaching, named
initiatives, with citations; what they already cover and the gap left for us; or
"not found in sources")
## Event Timeline
A table: | Event | When | Potential implications (derived only) |
## Where Entendido Could Fit
A table with the 2 or 3 most specific developments, each leading to a different need:
| Development | Specific capability need (derived) | Best-fit offering | How to tailor it | Explore in first call |
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
One sentence: the main reason. Do not write a number, score or High/Medium/Low label;
the scored table is added automatically.
## Sources
Numbered list, title and URL, only the ones you cited.

After the Sources list, end with ONE fenced ```json block (nothing after it) holding
chart data. Use only numbers and dates stated in the sources, each with its citation.
Leave a list empty rather than guessing.
```json
{{"key_numbers": [{{"label": "Employees", "value": "19,000", "note": "June 2026", "cite": [13]}}],
  "timeline": [{{"date": "2024-10", "event": "Acquired Bharat Serums", "type": "deal", "cite": [1]}}],
  "fit": [{{"criterion": "Evidence of need", "score": 3, "why": "one short line of evidence", "cite": [2]}},
          {{"criterion": "Capability fit", "score": 4, "why": "...", "cite": [5]}},
          {{"criterion": "Decision-maker access", "score": 2, "why": "...", "cite": []}},
          {{"criterion": "Urgency", "score": 4, "why": "...", "cite": [1]}},
          {{"criterion": "Commercial feasibility", "score": 3, "why": "...", "cite": []}}],
  "trend": {{"label": "Revenue (Rs crore)", "points": [{{"x": "FY24", "y": 10335}}], "cite": [4]}}
}}
```
Rules: key_numbers at most 4 (revenue, employees, deal size, market cap, growth...).
timeline up to 8 events, date as YYYY-MM or YYYY, type one of deal, leadership,
growth, legal. fit: exactly those five criteria in that order, scored 0 to 5 by the fit score framework
in the context; existing L&D lowers capability fit only if it already covers the need. trend: one financial
series with 2 to 6 points over time, all the same period type (all financial years or
all quarters) and the same unit, values exactly as the source states them; or null.

SOURCES:
{src_block}"""
    if prompt_only:
        return prompt
    return ask(prompt, models=DETAILED if detailed else FAST)


def slim(sources):
    return [{"title": x["title"], "url": x["url"], "date": x["date"]} for x in sources]


def run_stream(company, focus="", detailed=False):
    """Research a company and yield progress events as dicts:
    stage, hits, read, token (brief text as it is written), then done."""
    queries, news_queries = plan_queries(company, focus)
    yield {"t": "stage", "stage": "search", "msg": f"Searching with {len(queries) + len(news_queries)} queries"}
    hits = search(queries, news_queries)
    yield {"t": "hits", "items": slim(hits)}
    yield {"t": "stage", "stage": "read", "msg": f"Reading {len(hits)} pages"}
    fetched = parallel(lambda h: fetch_text(h["url"]), hits, workers=16, seconds=20,
                       default=lambda h: (h["url"], "", ""))
    sources, seen_final = [], set()
    for h, (final_url, text, published) in zip(hits, fetched):
        h["url"], h["text"] = final_url, text
        h["date"] = parse_date(h.get("date")) or published
        if h["url"] in seen_final or any(x in h["url"] for x in SKIP):
            continue
        seen_final.add(h["url"])
        if h["text"] or h["snippet"]:
            sources.append(h)
    # Newest first, undated last, so the writer sees the latest facts first and
    # can resolve conflicts by date.
    sources.sort(key=lambda x: x["date"] or "0000", reverse=True)
    yield {"t": "read", "count": len(sources)}
    yield {"t": "stage", "stage": "write", "msg": f"Writing the brief from {len(sources)} sources"}
    prompt = write_brief(company, focus, sources, detailed, prompt_only=True)
    parts = []
    for piece in ask_stream(prompt, models=DETAILED if detailed else FAST):
        parts.append(piece)
        yield {"t": "token", "text": piece}
    yield {"t": "stage", "stage": "charts", "msg": "Building charts"}
    brief, viz = split_viz("".join(parts))
    viz["coverage"] = coverage(sources)
    yield {"t": "done", "company": company, "brief": brief, "export": with_glance(brief, viz),
           "viz": viz, "sources": slim(sources), "_full_sources": sources}


def run(company, focus="", log=print, save=True, detailed=False):
    """Research a company. Returns (brief_markdown, sources, chart_data)."""
    done = None
    for ev in run_stream(company, focus, detailed):
        if ev["t"] == "stage":
            log(ev["msg"])
        elif ev["t"] == "done":
            done = ev
    brief, viz, sources = done["brief"], done["viz"], done["_full_sources"]
    if save:  # the Vercel filesystem is read-only, so the web version skips this
        slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
        os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
        path = os.path.join(HERE, "output", f"{slug}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(done["export"])
        with open(os.path.join(HERE, "output", f"{slug}.sources.json"), "w", encoding="utf-8") as f:
            json.dump(sources, f, indent=1, ensure_ascii=False)
        with open(os.path.join(HERE, "output", f"{slug}.viz.json"), "w", encoding="utf-8") as f:
            json.dump(viz, f, indent=1, ensure_ascii=False)
        log(f"Saved {path}")
    return brief, sources, viz


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    args = [a for a in sys.argv[1:] if a != "--detailed"]
    run(args[0], args[1] if len(args) > 1 else "", detailed="--detailed" in sys.argv)


if __name__ == "__main__":
    main()

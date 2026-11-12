# Entendido prospecting agents

Three agents that do the first pass of prospecting so the team only spends time on
companies worth a conversation. Built on a free stack: Tavily search (1,000 free searches a month), with SearchApi.io
and DuckDuckGo as fallbacks, plain page fetches, and the Gemini free tier.

| Agent | Status | What it does |
|---|---|---|
| 3. Brief writer | working | Company name in, one-page prospect brief out, in the Emcure/Gennova format with numbered sources |
| 1. Signal scanner | planned | Finds companies showing a trigger (M&A, founder exit, new CEO, IPO, market entry) in a date window |
| 2. Tender finder | planned, if wanted | Finds RFPs for behavioural training |

## Setup

```
pip install -r requirements.txt
```

Create a `.env` file next to the scripts:

```
GEMINI_API_KEY=your-key-from-aistudio.google.com/apikey
TAVILY_API_KEY=your-key-from-app.tavily.com
SERP_API_KEY=optional-key-from-searchapi.io
```

Search tries Tavily, then SearchApi, then DuckDuckGo. Tavily's free plan resets every
month. SearchApi's free plan is 100 searches in total and is already used up.
DuckDuckGo needs no key but is weaker on Indian business news.

`.env` is git-ignored. Never commit the key. For GitHub Actions, add it as a repository secret.

## Run Agent 3

```
python agent3_brief.py "Company name"
python agent3_brief.py "Emcure Pharmaceuticals" "the Gennova Biopharmaceuticals subsidiary"
python agent3_brief.py "Emcure Pharmaceuticals" --detailed
```

Default mode writes with `gemini-3.1-flash-lite` and takes about 20 seconds.
`--detailed` (or the tick box on the web page) uses `gemini-3.5-flash`, about a minute.

Output lands in `output/<company>.md` with the sources in `output/<company>.sources.json`.
A run uses about 10 searches and one Gemini call.

## Web page for the team (Vercel)

`public/index.html` is the page and `app.py` is the backend (a small Flask app). The team opens the
link, types a company and clicks "Write brief". The brief
shows on screen with Download and Copy buttons, under an "At a glance" panel:
key numbers, fit-signal scores, news coverage by month, an event timeline, and a
revenue trend when the sources give one. Every number carries its source number. A run takes about 20 seconds.

Deploy free on Vercel Hobby:

1. Go to vercel.com, sign in with GitHub, click "Add New", then "Project".
2. Import this repository. Leave Framework Preset as "Other" and the build settings empty.
3. Under Environment Variables add `GEMINI_API_KEY` and `TAVILY_API_KEY`.
4. Click Deploy. Every `git push` redeploys automatically.

The page has no password, so anyone with the link can use up the free API quota.
Share the link only inside the team. Vercel cuts a request off
at 5 minutes. Runs finish far inside that.

## Run Agent 3 from GitHub Actions

1. Keep this repository **private**. Briefs and `entendido_context.md` are internal.
2. In Settings, Secrets and variables, Actions, add `GEMINI_API_KEY` and `SERP_API_KEY`.
3. Open Actions, pick "Agent 3 - prospect brief", click "Run workflow", type the company.
4. When the run finishes, download the `brief` artifact from the run page.

Briefs are never committed. The `output/` folder is git-ignored.

## How Agent 3 works

1. Fixed search templates cover M&A, leadership change, HR head, expansion,
   restructuring, the annual report, leadership programmes and complaint matters.
2. A news search runs for the company, the focus unit, and "complaint case", so recent
   legal matters surface. Web search runs for every template. All searches run in
   parallel, and results are picked round-robin so each query gets a share.
3. Up to 30 pages are fetched in parallel and stripped to text. PDFs are read with pypdf.
   LinkedIn profiles, Twitter and salary sites are skipped.
4. Gemini writes the brief using only those sources, with a citation on every fact.

Rules baked into the prompt come from the Emcure and Zespri briefs and live in
`entendido_context.md`: facts versus derived implications, sell to a single leadership
group, watch for procurement and "we already do this", never mention legal matters,
and exclude existing clients. Edit that file to change how the agent thinks.

## Known limits

- Google Search grounding inside Gemini is not on the free tier, which is why search
  runs through SearchApi or DuckDuckGo instead.
- Some company websites block scripts, so their own annual reports and press releases
  cannot be read. Emcure's site does this. News coverage of the same material is
  usually picked up, but programme names that only appear in the annual report
  (for example Emcure's SAKSHAM and EmCoach) will be missed.
- The newest Gemini flash models sometimes return "high demand" errors. The script
  falls back between `gemini-3.1-flash-lite` and `gemini-3.5-flash` and retries.
- Names of HR heads often come from LinkedIn or aggregator sites. The brief marks
  these as needing verification, as the human-written samples do.

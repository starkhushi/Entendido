"""Web page for Agent 3. Run locally:  streamlit run app.py"""
import os
import streamlit as st

st.set_page_config(page_title="Entendido Prospect Briefs", page_icon="🧭", layout="centered")

# On Streamlit Cloud, keys come from the app's Secrets box; locally from .env.
for k in ("GEMINI_API_KEY", "SERP_API_KEY", "APP_PASSWORD"):
    try:
        if k in st.secrets and not os.environ.get(k):
            os.environ[k] = st.secrets[k]
    except Exception:
        pass

from agent3_brief import run  # noqa: E402  (needs the keys above)

st.title("Prospect brief writer")
st.caption("Type a company. The agent searches the web, reads about 30 pages and writes a "
           "one-page brief in the Emcure/Gennova format. It takes about 3 minutes.")

password = os.environ.get("APP_PASSWORD")
if password and st.session_state.get("ok") is not True:
    entered = st.text_input("Team password", type="password")
    if entered == password:
        st.session_state.ok = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    st.stop()

with st.form("brief"):
    company = st.text_input("Company", placeholder="e.g. Emcure Pharmaceuticals")
    focus = st.text_input("Focus (optional)", placeholder="e.g. the Gennova subsidiary")
    go = st.form_submit_button("Write brief", type="primary")

if go and company.strip():
    with st.status("Researching...", expanded=True) as status:
        try:
            brief, sources = run(company.strip(), focus.strip(), log=st.write)
            status.update(label="Brief ready", state="complete", expanded=False)
            st.session_state.result = (company.strip(), brief, sources)
        except Exception as e:
            status.update(label="Something went wrong", state="error")
            st.error(f"{type(e).__name__}: {str(e)[:300]}")
            st.info("If this says 429 or 503, Gemini is busy. Wait a minute and try again.")

if "result" in st.session_state:
    name, brief, sources = st.session_state.result
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    st.download_button("Download brief (.md)", brief, file_name=f"{slug}.md")
    st.divider()
    st.markdown(brief)
    with st.expander(f"All {len(sources)} pages the agent read"):
        for i, s in enumerate(sources, 1):
            st.markdown(f"{i}. [{s['title'] or s['url']}]({s['url']}) {s['date']}")

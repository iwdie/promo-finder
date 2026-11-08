"""Promo Finder web app.  Run locally:  streamlit run app.py
Upload episode screenplays -> ranked 20-60s promos with hook, cliffhanger and timestamps.
Uploaded files are processed in a temporary folder and deleted after the run; nothing is stored."""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.llm import LLM, DEFAULT_MODELS
from promo_finder.pipeline import PromoFinder

ROOT = Path(__file__).parent
st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")

# ---------------------------------------------------------------- sidebar: model settings
with st.sidebar:
    st.header("Model")
    provider = st.selectbox("Provider", ["none (rules only, free)", "gemini", "groq", "openai", "anthropic", "openrouter"],
                            help="Rules-only needs no key. Any AI provider adds the AI picker, montages and blind judge.")
    provider = provider.split(" ")[0]
    model, key, base_url = None, "", ""
    if provider != "none":
        default = "gemini-3.5-flash-lite" if provider == "gemini" else DEFAULT_MODELS.get(provider, "")
        model = st.text_input("Model", default)
        key = st.text_input("API key", type="password", help="Used only for this run, never stored. "
                            "Free Gemini key: aistudio.google.com/apikey")
        if provider == "openai":
            base_url = st.text_input("Base URL (any OpenAI-compatible API)", "")
    st.header("Options")
    per_ep = st.slider("Max promos per episode", 1, 3, 3)
    montages = st.slider("Cross-episode montages", 0, 5, 5, disabled=provider == "none")
    batch = st.slider("Episodes per AI call", 1, 10, 5, disabled=provider == "none",
                      help="5 keeps a 50-episode run near 17 calls (fits Gemini's free ~20 requests/day).")
    judge = st.checkbox("Blind AI judge on top 25", True, disabled=provider == "none")
    top_n = st.slider("Promos to show", 5, 50, 15)

# ---------------------------------------------------------------- main
st.title("🎬 Promo Finder")
st.write("Upload a microdrama's episode screenplays. The tool returns ranked **20–60 second promos**: "
         "episode, start/end of every segment, the hook (first 3 s), the cliffhanger (the cut) and why it works.")
with st.expander("How it works"):
    st.markdown(
        "1. **Rules (code):** finds clean cut points (camera cuts where nobody is mid-sentence) and scores every 20–60 s window.\n"
        "2. **AI picker:** reads the numbered screenplay and picks promos by *line ID*, so it never invents timestamps.\n"
        "3. **AI montages:** joins key moments across episodes.\n"
        "4. **Validation (code):** snaps to clean cuts, forces 20–60 s, rejects what it can't fix.\n"
        "5. **Blind AI judge:** re-scores the top picks seeing only what a viewer sees.\n"
        "6. **Rank:** quality + early-episode bonus − spoiler / final-act penalty, de-duplicated.\n\n"
        "Source: [github.com/iwdie/promo-finder](https://github.com/iwdie/promo-finder)")

files = st.file_uploader("Screenplays: a .zip or one or more .txt files", type=["zip", "txt"], accept_multiple_files=True)
c1, c2 = st.columns([1, 4])
run = c1.button("Find promos", type="primary", disabled=not files)
sample = c2.button("Try the built-in sample screenplay")

if (run and files) or sample:
    if provider != "none" and not key:
        st.warning("No API key entered, so this run uses the free rules-only mode.")
        provider = "none"
    work = Path(tempfile.mkdtemp(prefix="promo_app_"))
    inp, out = work / "input", work / "output"
    inp.mkdir()
    try:
        if sample:
            for f in (ROOT / "examples" / "sample_show").glob("*.txt"):
                shutil.copy(f, inp / f.name)
        else:
            for f in files:
                data = f.getvalue()
                if f.name.lower().endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        for m in z.namelist():
                            if m.lower().endswith(".txt") and not m.startswith("__MACOSX"):
                                (inp / Path(m).name).write_bytes(z.read(m))
                else:
                    (inp / Path(f.name).name).write_bytes(data)

        llm = None
        if provider != "none":
            llm = LLM(provider, model, base_url=base_url or None, api_key=key, cache_dir=None, max_retries=4)
        with st.status("Running...", expanded=True) as status:
            finder = PromoFinder(llm, per_episode=per_ep, montages=montages, judge=judge, batch=batch,
                                 judge_batch=13, workers=1, log=lambda m: st.write(str(m).strip()))
            try:
                meta = finder.run(str(inp), str(out), show_name="uploaded show")
            except SystemExit as e:          # e.g. no screenplays found
                status.update(label="Could not read the screenplays", state="error")
                st.error(str(e))
                st.stop()
            status.update(label=f"Done: {meta['promos']} promos from {meta['episodes']} episodes", state="complete", expanded=False)

        # cut list for the editor
        subprocess.run([sys.executable, str(ROOT / "cut_clips.py"), "--promos", str(out / "promos.json"),
                        "--out", str(out / "cut_list"), "--top", "10"], capture_output=True)

        st.session_state["result"] = {
            "meta": meta,
            "promos": json.loads((out / "promos.json").read_text(encoding="utf-8")),
            "files": {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)   # nothing uploaded is kept

res = st.session_state.get("result")
if res:
    meta, promos = res["meta"], res["promos"]
    m = st.columns(5)
    m[0].metric("Episodes", meta["episodes"])
    m[1].metric("Promos", meta["promos"])
    m[2].metric("AI picks / montages", f"{meta['by_source']['ai']} / {meta['by_source']['ai-montage']}")
    m[3].metric("Rejected by checks", meta["rejected"])
    m[4].metric("Model", meta["model"].split(":")[-1] if ":" in meta["model"] else "rules only")

    seg = lambda p: "\n".join(f"E{s['episode']} {s['start']}–{s['end']}" for s in p["segments"])
    df = pd.DataFrame([{
        "Rank": p["rank"], "Segments": seg(p), "Len (s)": round(p["duration_s"]), "Hook (first 3s)": p.get("hook", ""),
        "Cliffhanger (cut)": p.get("cliffhanger", ""), "Why": p.get("why", ""), "Score": p["final_score"],
        "Source": p["source"], "Judge note": (p.get("judge") or {}).get("note", ""),
    } for p in promos[:top_n]])
    st.subheader("Ranked promos (post from the top)")
    st.dataframe(df, hide_index=True, use_container_width=True,
                 column_config={"Hook (first 3s)": st.column_config.TextColumn(width="large"),
                                "Cliffhanger (cut)": st.column_config.TextColumn(width="large")})

    st.subheader("Downloads")
    d = st.columns(5)
    f = res["files"]
    d[0].download_button("promos.csv", f["promos.csv"], "promos.csv", "text/csv")
    d[1].download_button("report.md", f["report.md"], "report.md", "text/markdown")
    d[2].download_button("review_sheet.csv", f["review_sheet.csv"], "review_sheet.csv", "text/csv")
    if "cut_list/cut_list.csv" in f:
        d[3].download_button("cut_list.csv (top 10)", f["cut_list/cut_list.csv"], "cut_list.csv", "text/csv")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in f.items():
            z.writestr(name, data)
    d[4].download_button("All outputs (.zip)", buf.getvalue(), "promo_outputs.zip", "application/zip")

    with st.expander("Full report (top 15 with script excerpts)"):
        st.markdown(f["report.md"].decode("utf-8"))
    st.caption("Files you upload are processed in a temporary folder and deleted when the run finishes.")

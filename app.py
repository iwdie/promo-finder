"""Promo Finder web app.  Run locally:  streamlit run app.py
Upload episode screenplays -> ranked 20-60s promos with hook, cliffhanger and timestamps.
Uploaded files are processed in a temporary folder and deleted after the run; nothing is stored."""
import io
import json
import math
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.llm import LLM, DEFAULT_MODELS
from promo_finder.parser import load_show
from promo_finder.pipeline import PromoFinder, viewer_script

ROOT = Path(__file__).parent
st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")


def owner_key() -> str:
    """Optional demo key set by the app owner in Streamlit secrets (never shown)."""
    try:
        return st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        return ""


# ---------------------------------------------------------------- sidebar: model settings
DEMO = bool(owner_key())
with st.sidebar:
    st.header("Model")
    choices = (["gemini (built-in key, no setup)"] if DEMO else []) + \
              ["none (rules only, free)", "gemini (my own key)", "groq", "openai", "anthropic", "openrouter"]
    choice = st.selectbox("Provider", choices,
                          help="The built-in option needs no key. Rules-only is free and instant. "
                               "Any AI provider adds the AI picker, montages and blind judge.")
    use_demo = choice.startswith("gemini (built-in")
    provider = choice.split(" ")[0]
    model, key, base_url = None, "", ""
    if provider != "none":
        default = "gemini-3.5-flash-lite" if provider == "gemini" else DEFAULT_MODELS.get(provider, "")
        model = st.text_input("Model", default)
        if not use_demo:
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

# ---------------------------------------------------------------- header
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
        "6. **Rank:** quality + early-episode bonus − spoiler / final-act penalty, de-duplicated.\n"
        "7. **You:** tick the promos you'd post in the review table and download your graded sheet.\n\n"
        "Source: [github.com/iwdie/promo-finder](https://github.com/iwdie/promo-finder)")

files = st.file_uploader("Screenplays: a .zip or one or more .txt files", type=["zip", "txt"], accept_multiple_files=True)
c1, c2 = st.columns([1, 4])
run = c1.button("Find promos", type="primary", disabled=not files)
sample = c2.button("Try the built-in sample (3 short made-up episodes)")

# ---------------------------------------------------------------- run
if (run and files) or sample:
    if use_demo:
        key = owner_key()
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

        n_files = len(list(inp.glob("*.txt")))
        llm = None
        if provider != "none":
            llm = LLM(provider, model, base_url=base_url or None, api_key=key, max_retries=4,
                      cache_dir=str(Path(tempfile.gettempdir()) / "promo_finder_cache"))
        expected = (math.ceil(n_files / batch) + (1 if montages and n_files > 1 else 0) + (2 if judge else 0)) if llm else 1
        bar = st.progress(0.0, text="Starting...")
        done = {"n": 0}

        def log(msg):
            msg = str(msg).strip()
            if "AI" in msg:
                done["n"] += 1
                bar.progress(min(0.95, done["n"] / max(1, expected)), text=msg)
            elif msg.startswith("Loaded"):
                bar.progress(0.02, text=msg)

        with st.status("Running...", expanded=False) as status:
            finder = PromoFinder(llm, per_episode=per_ep, montages=montages, judge=judge, batch=batch,
                                 judge_batch=13, workers=1, log=log)
            try:
                meta = finder.run(str(inp), str(out), show_name="uploaded show")
            except SystemExit as e:          # e.g. no screenplays found
                bar.empty()
                status.update(label="Could not read the screenplays", state="error")
                st.error(str(e))
                st.stop()
            bar.progress(1.0, text="Done")
            status.update(label=f"Done: {meta['promos']} promos from {meta['episodes']} episodes", state="complete")

        subprocess.run([sys.executable, str(ROOT / "cut_clips.py"), "--promos", str(out / "promos.json"),
                        "--out", str(out / "cut_list"), "--top", "10"], capture_output=True)

        promos = json.loads((out / "promos.json").read_text(encoding="utf-8"))
        eps = {e.number: e for e in load_show(str(inp))}
        st.session_state["result"] = {
            "meta": meta, "promos": promos,
            "excerpts": {p["promo_id"]: viewer_script(p, eps, kinds=("action", "dialogue", "sound")) for p in promos[:50]},
            "ep_len": {n: e.duration for n, e in eps.items()},
            "files": {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()},
        }
        st.session_state.pop("review", None)
    finally:
        shutil.rmtree(work, ignore_errors=True)   # nothing uploaded is kept


# ---------------------------------------------------------------- results
def seg_text(p, sep="\n"):
    return sep.join(f"E{s['episode']} {s['start']}–{s['end']}" for s in p["segments"])


def timeline(p, ep_len):
    """One bar per segment showing where the clip sits inside its episode."""
    rows = []
    for s in p["segments"]:
        total = max(1.0, ep_len.get(s["episode"], s["end_s"]))
        left, width = 100 * s["start_s"] / total, max(1.5, 100 * (s["end_s"] - s["start_s"]) / total)
        rows.append(
            f"<div style='display:flex;align-items:center;gap:8px;margin:4px 0'>"
            f"<div style='width:46px;font-size:12px;color:#666'>E{s['episode']}</div>"
            f"<div style='position:relative;flex:1;height:14px;background:#e9edf2;border-radius:7px'>"
            f"<div style='position:absolute;left:{left:.1f}%;width:{width:.1f}%;height:14px;background:#1f4e79;border-radius:7px'></div></div>"
            f"<div style='width:120px;font-size:12px;color:#666'>{s['start']}–{s['end']}</div></div>")
    return "".join(rows)


res = st.session_state.get("result")
if res:
    meta, promos = res["meta"], res["promos"]
    m = st.columns(5)
    m[0].metric("Episodes", meta["episodes"])
    m[1].metric("Promos", meta["promos"])
    m[2].metric("AI picks / montages", f"{meta['by_source']['ai']} / {meta['by_source']['ai-montage']}")
    m[3].metric("Rejected by checks", meta["rejected"])
    m[4].metric("Model", meta["model"].split(":")[-1] if ":" in meta["model"] else "rules only")

    if meta.get("quota_exhausted") and meta.get("ai_calls_failed"):
        st.info("The free AI quota for today ran out partway through, so some episodes are ranked by the rules only. "
                "Try again tomorrow, or pick **gemini (my own key)** in the sidebar.")
    elif meta.get("models_used") and len(meta["models_used"]) > 1:
        st.caption(f"Switched models when a free daily quota ran out: {' → '.join(meta['models_used'])}")
    tab_rank, tab_detail, tab_dl = st.tabs(["Ranked promos & review", "Promo detail", "Downloads"])

    # ---- ranked table with editor review columns
    with tab_rank:
        st.caption("Post from the top. Tick **Would post** and add notes as an editor; download your graded sheet below.")
        if "review" not in st.session_state:
            st.session_state["review"] = pd.DataFrame([{
                "Would post": False, "Notes": "", "Rank": p["rank"], "ID": p["promo_id"], "Segments": seg_text(p),
                "Len (s)": round(p["duration_s"]), "Hook (first 3s)": p.get("hook", ""),
                "Cliffhanger (cut)": p.get("cliffhanger", ""), "Why": p.get("why", ""), "Score": p["final_score"],
                "Source": p["source"], "Judge note": (p.get("judge") or {}).get("note", "") or "",
            } for p in promos[:50]])
        df = st.session_state["review"].head(top_n)
        locked = [c for c in df.columns if c not in ("Would post", "Notes")]
        edited = st.data_editor(df, hide_index=True, use_container_width=True, disabled=locked, key="review_editor",
                                column_config={"Would post": st.column_config.CheckboxColumn(width="small"),
                                               "Notes": st.column_config.TextColumn(width="medium"),
                                               "Hook (first 3s)": st.column_config.TextColumn(width="large"),
                                               "Cliffhanger (cut)": st.column_config.TextColumn(width="large")})
        picked = int(edited["Would post"].sum())
        st.write(f"Editor picks: **{picked} of {len(edited)}** shown"
                 + (f" (precision@{len(edited)} = {picked / len(edited):.0%})" if len(edited) else ""))
        st.download_button("Download my review (.csv)", edited.to_csv(index=False).encode("utf-8"),
                           "reviewed_promos.csv", "text/csv")

    # ---- detail view
    with tab_detail:
        label = lambda p: p.get("title") if p["source"] != "rules" and p.get("title") else f"{p['source']} pick"
        opts = {f"#{p['rank']} · {seg_text(p, ' + ')} · {label(p)}": p for p in promos[:50]}
        p = opts[st.selectbox("Promo", list(opts))]
        left, right = st.columns([3, 2])
        with left:
            st.markdown(f"**Hook (first 3 s):** {p.get('hook', '')}")
            st.markdown(f"**Cliffhanger (the cut):** {p.get('cliffhanger', '')}")
            if p.get("why"):
                st.markdown(f"**Why it works:** {p['why']}")
            j = p.get("judge") or {}
            if j.get("note"):
                st.markdown(f"**Blind judge:** {j['note']}")
            if p.get("flags"):
                st.markdown("**Fixed or flagged by code:** " + "; ".join(p["flags"]))
        with right:
            r, s = p.get("rule_scores") or {}, p.get("scores") or {}
            fmt = lambda v: "–" if v is None else str(v)
            st.dataframe(pd.DataFrame({
                "Rules": [fmt(r.get("hook")), fmt(r.get("cliffhanger")), fmt(r.get("standalone"))],
                "AI picker": [fmt(s.get("hook")), fmt(s.get("cliffhanger")), fmt(s.get("standalone"))],
                "Blind judge": [fmt(j.get("hook")), fmt(j.get("cliffhanger")), fmt(j.get("standalone"))],
            }, index=["Hook", "Cliffhanger", "Standalone"]), use_container_width=True)
            st.caption(f"Final score {p['final_score']} · {p['duration_s']:.0f}s · source: {p['source']}")
        st.markdown("**Where it sits in the episode**")
        st.markdown(timeline(p, res["ep_len"]), unsafe_allow_html=True)
        st.markdown("**What the viewer sees and hears** (`>>` first 3 s, `<<` last 4 s)")
        st.code(res["excerpts"].get(p["promo_id"], ""), language=None)

    # ---- downloads
    with tab_dl:
        f = res["files"]
        d = st.columns(3)
        d[0].download_button("promos.csv (all, ranked)", f["promos.csv"], "promos.csv", "text/csv")
        d[1].download_button("report.md (top 15 + excerpts)", f["report.md"], "report.md", "text/markdown")
        d[2].download_button("review_sheet.csv (blank)", f["review_sheet.csv"], "review_sheet.csv", "text/csv")
        d = st.columns(3)
        if "cut_list/cut_list.csv" in f:
            d[0].download_button("cut_list.csv (top 10)", f["cut_list/cut_list.csv"], "cut_list.csv", "text/csv")
            d[1].download_button("cut_commands.sh (ffmpeg)", f["cut_list/cut_commands.sh"], "cut_commands.sh", "text/plain")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in f.items():
                z.writestr(name, data)
        d[2].download_button("Everything (.zip)", buf.getvalue(), "promo_outputs.zip", "application/zip")
        st.caption("Uploaded files are deleted when the run finishes. AI replies are kept on the server for a while, "
                   "so re-running the same files is instant and uses no quota.")

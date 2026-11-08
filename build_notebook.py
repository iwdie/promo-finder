"""Regenerates promo_finder_colab.ipynb with the current code embedded (so the notebook needs no repo access)."""
import json
from pathlib import Path

ROOT = Path(__file__).parent
FILES = ["promo_finder/__init__.py", "promo_finder/parser.py", "promo_finder/rules.py", "promo_finder/llm.py",
         "promo_finder/prompts.py", "promo_finder/pipeline.py", "promo_finder/mock.py", "find_promos.py", "cut_clips.py"]

md = lambda s: {"cell_type": "markdown", "metadata": {}, "source": s.strip().splitlines(True)}
code = lambda s, hide=False: {"cell_type": "code", "metadata": ({"cellView": "form"} if hide else {}),
                              "execution_count": None, "outputs": [], "source": s.strip().splitlines(True)}

cells = [
    md("""
# Promo Finder: microdrama screenplay → ranked promos
Reads every episode screenplay and returns ranked **20–60s promo clips**: episode, start/end of each segment, the hook, the cliffhanger and one line on why it works.

**How it works**
1. **Rules (no AI):** finds every clean cut point (camera-shot boundaries where nobody is mid-sentence) and scores every 20–60s window on hook / cliffhanger / standalone. Runs free and instantly; it's the baseline and the safety net.
2. **AI per episode:** the model reads the numbered screenplay and proposes promos by **line ID**, never by typing timestamps.
3. **AI cross-episode montages:** joins 2–4 key moments from different episodes.
4. **Code validation:** every AI pick is snapped to clean cuts and forced to 20–60s; bad picks are logged in `rejected.json`.
5. **Blind AI judge:** a second pass re-scores the top picks seeing only what a viewer would see.
6. **Rank:** quality + small bonus for early episodes, minus spoiler risk, max 2 promos per episode in the top 10.

**Run:** Runtime → Run all. You'll be asked to upload the screenplay zip and for an API key (free Gemini key: https://aistudio.google.com/apikey).
"""),
    md("## 1. Code (embedded; same as the GitHub repo)"),
]
for f in FILES:
    cells.append(code(f"%%writefile {f}\n" + (ROOT / f).read_text(encoding="utf-8"), hide=True))
cells.insert(2, code("!mkdir -p promo_finder && pip -q install requests pandas"))

cells += [
    md("## 2. Upload the screenplays\nUpload the input-pack **.zip** (or the .txt files). Files stay inside this Colab session."),
    code("""
import os, zipfile, glob
from google.colab import files
os.makedirs("input", exist_ok=True)
for name, data in files.upload().items():
    path = os.path.join("input", name)
    open(path, "wb").write(data)
    if name.lower().endswith(".zip"):
        zipfile.ZipFile(path).extractall("input")
print(len(glob.glob("input/**/*.txt", recursive=True)), "screenplay files found")
"""),
    md("## 3. Pick the model\nChange `PROVIDER` / `MODEL` to swap models, no code changes. Options: `gemini`, `groq`, `openai`, `openrouter`, `anthropic`, or any OpenAI-compatible API via `BASE_URL`. Use `none` to run rules only."),
    code("""
PROVIDER = "gemini"            #@param ["gemini", "groq", "openai", "openrouter", "anthropic", "none"]
MODEL = "gemini-3.5-flash-lite"  #@param {type:"string"}
BASE_URL = ""                  #@param {type:"string"}
RPM = 10                       #@param {type:"number"}
BATCH = 5                      #@param {type:"number"}
EPISODES = ""                  #@param {type:"string"}
SHOW = ""                      #@param {type:"string"}

import os, getpass
ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
       "openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(PROVIDER)
if ENV and not os.environ.get(ENV):
    try:
        from google.colab import userdata
        os.environ[ENV] = userdata.get(ENV)          # Colab "Secrets" panel (key icon on the left)
    except Exception:
        os.environ[ENV] = getpass.getpass(f"{ENV}: ")
print("Using", PROVIDER, MODEL)
"""),
    code("""
#@title (optional) list the Gemini models your key can use
import requests, os
if PROVIDER == "gemini":
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}).json()
    print([m["name"].split("/")[-1] for m in r.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])])
""", hide=True),
    md("## 4. Run\nRules-only first (seconds), then the AI run (5 episodes per call: ~17 calls, fits the 20-requests/day free tier; ~1 min)."),
    code("""
!python find_promos.py --input input --out outputs/rules_only --provider none --show "$SHOW"
"""),
    code("""
import shlex, subprocess
cmd = f"python find_promos.py --input input --out outputs/ai_{PROVIDER} --provider {PROVIDER} --model {shlex.quote(MODEL)} --rpm {RPM} --batch {BATCH} --judge-batch 13 " + (f" --show {shlex.quote(SHOW)}" if SHOW else "")
if BASE_URL: cmd += f" --base-url {shlex.quote(BASE_URL)}"
if EPISODES: cmd += f" --episodes {EPISODES}"
if PROVIDER != "none":
    !{cmd}
"""),
    md("## 5. Results"),
    code("""
import pandas as pd, json
out = f"outputs/ai_{PROVIDER}" if PROVIDER != "none" else "outputs/rules_only"
print(json.dumps(json.load(open(f"{out}/run_meta.json")), indent=2))
df = pd.read_csv(f"{out}/promos.csv")
pd.set_option("display.max_colwidth", 80)
df[["rank", "source", "segments", "duration_s", "hook", "cliffhanger", "why", "final_score"]].head(15)
"""),
    code("""
from IPython.display import Markdown
Markdown(open(f"{out}/report.md").read()[:20000])
"""),
    md("## 6. Cut list for the editor\nWrites `cut_list.csv` + `cut_commands.sh` for the top 10. Add `--videos <folder>` to cut real clips if you have the episode videos."),
    code("""
!python cut_clips.py --promos {out}/promos.json --out outputs/clips --top 10
"""),
    code("""
!cd outputs && zip -qr ../promo_outputs.zip . && cd ..
files.download("promo_outputs.zip")
"""),
]

nb = {"nbformat": 4, "nbformat_minor": 5, "cells": cells,
      "metadata": {"colab": {"provenance": []}, "kernelspec": {"name": "python3", "display_name": "Python 3"},
                   "language_info": {"name": "python"}}}
(ROOT / "promo_finder_colab.ipynb").write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote promo_finder_colab.ipynb with", len(cells), "cells")

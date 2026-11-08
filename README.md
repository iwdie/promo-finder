# Promo Finder

Reads a microdrama's episode screenplays and returns ranked **20–60 second promo clips**. Each promo comes with its episode(s), start and end time for every segment, the hook, the cliffhanger and one line on why it works.

Built for the Kuku AI Generalist assignment (Problem 1).

## Quick start

**Colab (easiest):** open `promo_finder_colab.ipynb` in Google Colab and choose Runtime → Run all. It asks for the screenplay zip and an API key.

**Local:**
```bash
pip install -r requirements.txt
# --input takes a folder, a .zip of screenplays, or one .txt file
# rules only: free, no key, ~10s for 50 episodes
python find_promos.py --input scripts.zip --out outputs/rules_only --provider none
# with AI (free Gemini key from https://aistudio.google.com/apikey). No key set -> falls back to rules only.
export GEMINI_API_KEY=...
python find_promos.py --input scripts.zip --out outputs/ai --model gemini-3.5-flash-lite
# turn the top 10 into a cut list + ffmpeg script, or real .mp4 clips if the episode videos exist
python cut_clips.py --promos outputs/ai/promos.json --out clips --top 10 [--videos path/to/episodes]
# try it without the confidential pack
python find_promos.py --input examples/sample_show --out outputs/sample --provider none
python -m pytest -q
```

## Swapping models
The tool calls models through one function, `LLM.complete(system, user)`, using plain HTTP with no SDK. To change the model, change the flags; no code changes are needed.

| Provider | Flags | Key env var |
|---|---|---|
| Gemini | `--provider gemini --model gemini-3.5-flash` | `GEMINI_API_KEY` |
| Groq | `--provider groq --model llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| OpenAI / any OpenAI-compatible API | `--provider openai --base-url https://.../v1 --model ...` | `OPENAI_API_KEY` |
| Anthropic | `--provider anthropic --model ...` | `ANTHROPIC_API_KEY` |
| Local (Ollama) | `--provider ollama --model llama3.1` | – |
| Rules only | `--provider none` | – |

Responses are cached in `.cache/`, so a re-run with the same model costs nothing. Free Gemini keys allow about 20 requests a day per model, so by default 5 episodes go in one call (`--batch 5`) and a full 50-episode run takes about 17 calls. With a paid key, use `--batch 1 --workers 8` for one episode per call in parallel.

## Pipeline
1. **Parse:** turns each screenplay into timestamped events (camera, action, dialogue, sound).
2. **Clean cut points (code):** camera-shot and scene boundaries where nobody is mid-sentence.
3. **Rules baseline (code):** scores every 20–60s window between clean cuts on:
   - hook: what happens in the first 3s
   - cliffhanger: the last 4s, a question before the answer, a sound sting, the episode's end
   - standalone: starts at a scene, doesn't open on a reply
   - intensity
4. **AI per episode:** the model reads the numbered screenplay and picks promos by **line ID** (`from_id`/`to_id`). Code turns those IDs into timestamps, so the model never types a timestamp. It also tags key moments.
5. **AI montages:** joins 2–4 key moments from different episodes into one promo, again by ID.
6. **Validation (code):**
   - snaps starts earlier and ends later to the nearest clean cut
   - trims or extends to fit 20–60s
   - rejects anything it can't fix (logged in `rejected.json`)
   - flags promos with no spoken or action beat in the first 3s
7. **Blind AI judge:** re-scores the top 25 promos. It sees only what a viewer would see and hear, not the first pass's reasoning.
8. **Rank:**
   - 0.8 × AI quality (hook 40%, cliffhanger 35%, standalone 25%) + 0.2 × rule score
   - bonus for early episodes, since new viewers land on Episode 1
   - penalty for spoilers
   - near-duplicates removed, and no more than 2 promos per episode in the top 10

## From promo list to clips (`cut_clips.py`)
Finding the promos is the job; cutting them is the editor's. To save them a step, `cut_clips.py` writes `cut_list.csv` (every segment of the top promos, in order) and `cut_commands.sh` (ready ffmpeg commands). If you pass `--videos`, it cuts and joins the clips itself (`P001.mp4`, ...). It re-encodes so cuts land on the exact frame. Tested on dummy videos: clip lengths matched the promo list within 0.1s.

## Outputs (per run)
| File | What it is |
|---|---|
| `promos.csv` / `promos.json` | all valid promos, ranked, with segments, hook, cliffhanger, why, scores and flags |
| `report.md` | top 10 table and the top 15 with the script excerpt a viewer would see (`>>` first 3s, `<<` last 4s) |
| `review_sheet.csv` | top 20 with blank columns for an editor to grade (the real quality check) |
| `rejected.json` | AI picks that failed validation, with the reason |
| `raw_llm/` | unedited model replies |
| `run_meta.json` | model, counts, AI/rules agreement, calls and approximate tokens |

## Notes
- Do not commit the Kuku input pack or its outputs to a public repo; they are confidential. `.gitignore` excludes them.
- Timestamps come from the screenplay, not the final edit. If the cut differs, an editor nudges them by a second or two.
- `promo_finder_colab.ipynb` is generated from the source by `python build_notebook.py`.

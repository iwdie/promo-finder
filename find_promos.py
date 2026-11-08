#!/usr/bin/env python3
"""Find and rank promo clips in a microdrama screenplay folder.

Examples
  python find_promos.py --input ./scripts --out ./outputs --provider none          # rules only, no key
  python find_promos.py --input ./scripts --out ./outputs --provider gemini         # needs GEMINI_API_KEY
  python find_promos.py --input ./scripts --out ./outputs --provider groq --model llama-3.3-70b-versatile
  python find_promos.py --input ./scripts --out ./outputs --provider openai --base-url https://YOUR-API/v1 --model YOUR-MODEL
"""
import argparse

from promo_finder.llm import LLM
from promo_finder.pipeline import PromoFinder


def parse_eps(s: str | None):
    if not s:
        return None
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="folder (or unzipped pack) of episode .txt screenplays")
    ap.add_argument("--out", default="outputs", help="output folder")
    ap.add_argument("--provider", default="gemini", help="gemini | groq | openai | openrouter | anthropic | ollama | mock | none")
    ap.add_argument("--model", default=None, help="model name (default depends on provider)")
    ap.add_argument("--base-url", default=None, help="for any OpenAI-compatible API")
    ap.add_argument("--episodes", default=None, help="e.g. 1-10 or 1,4,7 (default: all)")
    ap.add_argument("--per-episode", type=int, default=3, help="max promos per episode")
    ap.add_argument("--montages", type=int, default=5, help="max cross-episode promos (0 = off)")
    ap.add_argument("--no-judge", action="store_true", help="skip the blind AI re-scoring pass")
    ap.add_argument("--rpm", type=float, default=8, help="max AI calls per minute (free tiers are rate limited)")
    ap.add_argument("--workers", type=int, default=1, help="parallel AI calls for episodes (raise if your quota allows)")
    ap.add_argument("--batch", type=int, default=1, help="episodes per AI call (e.g. 5 to stay inside a 20-requests/day free tier)")
    ap.add_argument("--judge-batch", type=int, default=5, help="promos per judge call")
    ap.add_argument("--show", default=None, help="show name for prompts/report")
    a = ap.parse_args()

    llm = None
    if a.provider.lower() != "none":
        llm = LLM(a.provider, a.model, base_url=a.base_url, min_interval=60.0 / a.rpm if a.rpm else 0.0,
                  cache_dir=".cache" if a.provider != "mock" else None)
    PromoFinder(llm, per_episode=a.per_episode, montages=a.montages, judge=not a.no_judge,
                workers=a.workers, batch=a.batch, judge_batch=a.judge_batch).run(
        a.input, a.out, episodes=parse_eps(a.episodes), show_name=a.show)


if __name__ == "__main__":
    main()

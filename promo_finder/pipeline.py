"""End-to-end: screenplays -> ranked promos + raw outputs.

    rules (always)  ->  AI per episode  ->  AI cross-episode montages
         \\                    |                     /
          +--> code validation (snap to clean cuts, 20-60s) --> AI judge (blind) --> rank --> files
"""
from __future__ import annotations

import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import prompts
from .llm import LLM
from .parser import Episode, load_show, fmt
from .rules import (baseline_candidates, cut_points, heuristic_scores, hook_score, cliff_score,
                    standalone_score, overlap, validate_promo, HOOK_WINDOW)


# --------------------------------------------------------------------------
def numbered_transcript(ep: Episode) -> str:
    return "\n".join(f"#{e.idx} {e.line()}" for e in ep.events)


def ids_to_segment(ep: Episode, a, b) -> dict:
    a, b = int(a), int(b)
    if not (0 <= a < len(ep.events) and 0 <= b < len(ep.events)):
        raise ValueError(f"line id out of range ({a},{b})")
    if b < a:
        a, b = b, a
    seg_events = ep.events[a:b + 1]
    return {"episode": ep.number, "start_s": ep.events[a].start, "end_s": max(e.end for e in seg_events)}


def rule_view(promo: dict, eps: dict[int, Episode]) -> dict:
    """Rule scores for any promo (multi-segment: hook from first segment, cliffhanger from last)."""
    segs = promo["segments"]
    f, l = segs[0], segs[-1]
    if len(segs) == 1:
        return heuristic_scores(eps[f["episode"]], f["start_s"], f["end_s"])
    h, hb = hook_score(eps[f["episode"]], f["start_s"])
    c, cb = cliff_score(eps[l["episode"]], l["start_s"], l["end_s"])
    st = sum(standalone_score(eps[s["episode"]], s["start_s"], s["end_s"]) for s in segs) / len(segs)
    return {"hook": round(h, 1), "cliffhanger": round(c, 1), "standalone": round(st, 1), "intensity": None,
            "total": round(0.35 * h + 0.35 * c + 0.2 * st + 0.1 * 5, 2), "hook_beat": hb, "cliff_beat": cb}


def viewer_script(promo: dict, eps: dict[int, Episode], kinds=("action", "dialogue", "sound", "camera")) -> str:
    """What the viewer sees/hears, in order, with >> for the first 3s and << for the last 4s."""
    out, clock = [], 0.0
    total = promo["duration_s"]
    for seg in promo["segments"]:
        ep = eps[seg["episode"]]
        out.append(f"--- E{ep.number} {seg['start']}-{seg['end']} ---")
        for e in ep.events:
            if e.kind in kinds and e.start >= seg["start_s"] - 0.05 and e.start < seg["end_s"] - 0.05:
                t = clock + (e.start - seg["start_s"])
                mark = ">>" if t < HOOK_WINDOW else ("<<" if t >= total - 4.0 else "  ")
                out.append(f"{mark} {e.line()}")
        clock += seg["end_s"] - seg["start_s"]
    return "\n".join(out)


# --------------------------------------------------------------------------
class PromoFinder:
    def __init__(self, llm: LLM | None = None, per_episode: int = 3, montages: int = 5, judge: bool = True,
                 judge_top: int = 25, workers: int = 1, batch: int = 1, judge_batch: int = 5, log=print):
        self.llm, self.k, self.n_montages, self.judge, self.judge_top, self.log = llm, per_episode, montages, judge, judge_top, log
        self.workers = max(1, workers)
        self.batch = max(1, batch)              # episodes per AI call (fewer calls on tight free quotas)
        self.judge_batch = max(1, judge_batch)

    def run(self, input_dir: str, out_dir: str, episodes: list[int] | None = None, show_name: str | None = None) -> dict:
        t0 = time.time()
        out = Path(out_dir)
        (out / "raw_llm").mkdir(parents=True, exist_ok=True)
        eps_list = load_show(input_dir)
        if episodes:
            eps_list = [e for e in eps_list if e.number in set(episodes)]
        if not eps_list:
            raise SystemExit(f"No screenplays with timestamped lines found in {input_dir}. Expected blocks like "
                             f"'**NAME (Delivery):**' followed by 'Start: (00:01.00) | End: (00:03.00)'.")
        eps = {e.number: e for e in eps_list}
        cuts = {n: cut_points(e) for n, e in eps.items()}
        show = show_name or Path(input_dir).name
        premise = eps_list[0].logline
        self.log(f"Loaded {len(eps)} episodes ({sum(e.duration for e in eps_list)/60:.1f} min of screen time)")

        promos, rejected = [], []

        # 1) rules baseline (always runs; also the fallback when no API key)
        for ep in eps_list:
            for c in baseline_candidates(ep, cuts[ep.number], self.k):
                v, why = validate_promo(c, eps, cuts)
                (promos.append(v) if v else rejected.append({"source": "rules", "reason": why, "promo": c}))

        # 2) AI per episode
        moments = {}
        if self.llm:
            def ask(group):
                nums = ", ".join(str(e.number) for e in group)
                self.log(f"  AI: episode(s) {nums}")
                if len(group) == 1:
                    ep = group[0]
                    user = prompts.EPISODE.format(show=show, premise=premise, num=ep.number, logline=ep.logline,
                                                  transcript=numbered_transcript(ep), k=self.k)
                else:
                    blocks = "\n\n".join(f"===== EPISODE {e.number} =====\nLogline: {e.logline}\n{numbered_transcript(e)}" for e in group)
                    user = prompts.EPISODE_BATCH.format(show=show, premise=premise, n=len(group), blocks=blocks, k=self.k)
                try:
                    return self.llm.complete_json(prompts.SYSTEM, user)
                except Exception as e:  # keep going; report it
                    return e
            groups = [eps_list[i:i + self.batch] for i in range(0, len(eps_list), self.batch)]
            if self.workers == 1:
                answers = [ask(g) for g in groups]
            else:
                with ThreadPoolExecutor(max_workers=self.workers) as pool:   # episodes are independent
                    answers = list(pool.map(ask, groups))
            per_ep_answers = []
            for group, ans in zip(groups, answers):
                tag = f"{group[0].number:03d}" + (f"-{group[-1].number:03d}" if len(group) > 1 else "")
                if isinstance(ans, Exception):
                    for ep in group:
                        rejected.append({"source": "ai", "episode": ep.number, "reason": f"LLM error: {ans}"})
                    continue
                data, raw = ans
                if isinstance(data, list):           # some models drop the outer object
                    data = {"episodes": data} if len(group) > 1 else (data[0] if data else {})
                (out / "raw_llm" / f"episode_{tag}.json").write_text(raw, encoding="utf-8")
                if "episodes" in data:
                    by_num = {}
                    for d in data["episodes"]:
                        m = re.search(r"\d+", str(d.get("episode", ""))) if isinstance(d, dict) else None
                        if m:
                            by_num[int(m.group())] = d
                    for ep in group:
                        per_ep_answers.append((ep, by_num.get(ep.number, {})))
                else:
                    per_ep_answers.append((group[0], data))
            for ep, data in per_ep_answers:
                for p in data.get("promos", [])[: self.k]:
                    try:
                        segs = [ids_to_segment(ep, s["from_id"], s["to_id"]) for s in p.get("segments", [])]
                    except (KeyError, ValueError, TypeError) as e:
                        rejected.append({"source": "ai", "episode": ep.number, "reason": f"bad segment ids: {e}", "promo": p})
                        continue
                    cand = {**p, "source": "ai", "segments": segs}
                    v, why = validate_promo(cand, eps, cuts)
                    (promos.append(v) if v else rejected.append({"source": "ai", "episode": ep.number, "reason": why, "promo": p}))
                for j, m in enumerate(data.get("key_moments", [])[:4], 1):
                    try:
                        seg = ids_to_segment(ep, m["from_id"], m["to_id"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    if 2 <= seg["end_s"] - seg["start_s"] <= 20:
                        moments[f"M{ep.number}_{j}"] = {**m, **seg}

        # 3) AI cross-episode montages
        if self.llm and self.n_montages and len(moments) >= 2:
            self.log(f"  AI: montages from {len(moments)} key moments")
            lines = []
            for mid, m in moments.items():
                ep = eps[m["episode"]]
                said = " / ".join(f'{e.speaker}: {e.text}' for e in ep.events
                                  if e.kind == "dialogue" and m["start_s"] - 0.05 <= e.start < m["end_s"])
                lines.append(f"{mid}: E{m['episode']} [{fmt(m['start_s'])}-{fmt(m['end_s'])}] "
                             f"({m['end_s'] - m['start_s']:.1f}s) {m.get('type', '')}: {m.get('summary', '')} | {said[:300]}")
            try:
                data, raw = self.llm.complete_json(prompts.SYSTEM, prompts.MONTAGE.format(
                    show=show, premise=premise, moments="\n".join(lines), k=self.n_montages))
                (out / "raw_llm" / "montages.json").write_text(raw, encoding="utf-8")
                if isinstance(data, list):
                    data = {"montages": data}
                for p in data.get("montages", [])[: self.n_montages]:
                    ids = [i for i in p.get("moment_ids", []) if i in moments]
                    if len(ids) < 2:
                        rejected.append({"source": "ai-montage", "reason": "unknown or too few moment ids", "promo": p})
                        continue
                    segs = [{k: moments[i][k] for k in ("episode", "start_s", "end_s")} for i in ids]
                    v, why = validate_promo({**p, "source": "ai-montage", "segments": segs, "moment_ids": ids}, eps, cuts)
                    (promos.append(v) if v else rejected.append({"source": "ai-montage", "reason": why, "promo": p}))
            except Exception as e:
                rejected.append({"source": "ai-montage", "reason": f"LLM error: {e}"})

        # 4) rule scores for everything + agreement between AI and rules
        for p in promos:
            p["rule_scores"] = rule_view(p, eps)
        rule_ps = [p for p in promos if p["source"] == "rules"]
        for p in promos:
            if p["source"] == "ai":
                p["also_found_by_rules"] = any(overlap(p, r) >= 0.5 for r in rule_ps)

        # 5) preliminary score, then blind AI judge. Self-scores are inflated (the picker rates its own
        #    picks 8-9), so keep judging until every promo in the top `judge_top` has a blind score.
        last_ep = max(eps)
        for p in promos:
            p["season_position"] = round(p["segments"][0]["episode"] / last_ep, 2)
            p["final_score"] = self._score(p)
        promos.sort(key=lambda p: -p["final_score"])
        if self.llm and self.judge:
            raws = []
            for _ in range(4):
                todo = [p for p in promos[: self.judge_top] if not p.get("judge")]
                if not todo:
                    break
                raws += self._judge(todo, eps, self.judge_batch)
                for p in promos:
                    p["final_score"] = self._score(p)
                promos.sort(key=lambda p: -p["final_score"])
            (out / "raw_llm" / "judge.json").write_text(json.dumps(raws, indent=2, ensure_ascii=False), encoding="utf-8")

        # 6) when AI and rules found the same moment, keep the AI version (better hook/why text)
        #    and note the agreement; then rank with diversity
        ai_ps = [p for p in promos if p["source"] != "rules"]
        promos = [p for p in promos if p["source"] != "rules" or not any(overlap(p, q) >= 0.5 for q in ai_ps)]

        def dup(p, q):  # montages only compete with montages
            if (p["source"] == "ai-montage") != (q["source"] == "ai-montage"):
                return False
            return overlap(p, q) >= 0.5

        ranked, per_ep = [], {}
        for p in sorted(promos, key=lambda p: -p["final_score"]):
            if any(dup(p, q) for q in ranked):
                p["dropped_as_duplicate"] = True
                continue
            first_ep = p["segments"][0]["episode"]
            if len(ranked) < 10 and per_ep.get(first_ep, 0) >= 2:
                continue
            per_ep[first_ep] = per_ep.get(first_ep, 0) + 1
            ranked.append(p)
        leftovers = [p for p in promos if p not in ranked and not p.get("dropped_as_duplicate")]
        ranked += sorted(leftovers, key=lambda p: -p["final_score"])
        for i, p in enumerate(ranked, 1):
            p["rank"] = i
            p["promo_id"] = f"P{i:03d}"

        meta = self._write(out, ranked, rejected, eps, show, time.time() - t0)
        self.log(f"Done: {len(ranked)} promos, {len(rejected)} rejected -> {out}")
        return meta

    # ----------------------------------------------------------------------
    @staticmethod
    def _score(p: dict) -> float:
        r = p["rule_scores"]
        ai = p.get("judge") or p.get("scores")
        if ai:
            q = 0.4 * float(ai.get("hook", 5)) + 0.35 * float(ai.get("cliffhanger", 5)) + 0.25 * float(ai.get("standalone", 5))
            score = 0.8 * q + 0.2 * r["total"]
        else:
            score = r["total"]
        risk = ((p.get("judge") or {}).get("spoiler_risk") or p.get("spoiler_risk") or "low").lower()
        score -= {"high": 1.0, "medium": 0.3}.get(risk, 0.0)
        first_ep = p["segments"][0]["episode"]
        score += 0.5 if first_ep <= 3 else (0.25 if first_ep <= 10 else 0.0)   # early = matches what a new viewer sees first
        if p.get("season_position", 0) > 0.8:
            score -= 0.6                                                       # final act resolves the main conflict
        if p.get("also_found_by_rules"):
            score += 0.3                                                       # two independent pickers agree
        return round(score, 2)

    def _judge(self, top: list[dict], eps, batch: int = 5) -> list:
        self.log(f"  AI judge: {len(top)} promos")
        raws = []
        for i in range(0, len(top), batch):
            chunk = top[i:i + batch]
            blocks = []
            for j, p in enumerate(chunk, 1):
                blocks.append(f"### P{j} ({p['duration_s']:.0f}s)\n{viewer_script(p, eps, kinds=('action', 'dialogue', 'sound'))}")
            try:
                data, raw = self.llm.complete_json(prompts.SYSTEM, prompts.JUDGE.format(promos="\n\n".join(blocks)))
                raws.append(data)
            except Exception as e:
                self.log(f"  judge failed: {e}")
                continue
            for r in (data if isinstance(data, list) else data.get("ratings", [])):
                try:
                    k = int(str(r.get("id", "")).lstrip("P")) - 1
                    chunk[k]["judge"] = {x: r.get(x) for x in ("hook", "cliffhanger", "standalone", "spoiler_risk", "note")}
                except (ValueError, IndexError):
                    pass
        return raws

    def _write(self, out: Path, ranked, rejected, eps, show, secs) -> dict:
        seg_str = lambda p: " | ".join(f"E{s['episode']} {s['start']}-{s['end']}" for s in p["segments"])
        clean = lambda p: {k: v for k, v in p.items() if k not in ("dropped_as_duplicate",)}
        (out / "promos.json").write_text(json.dumps([clean(p) for p in ranked], indent=2, ensure_ascii=False), encoding="utf-8")
        (out / "rejected.json").write_text(json.dumps(rejected, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        cols = ["rank", "promo_id", "source", "segments", "duration_s", "hook", "cliffhanger", "why", "final_score",
                "ai_hook", "ai_cliffhanger", "ai_standalone", "judge_hook", "judge_cliffhanger", "judge_standalone",
                "judge_note", "rule_total", "spoiler_risk", "also_found_by_rules", "flags"]
        with open(out / "promos.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for p in ranked:
                s, j = p.get("scores") or {}, p.get("judge") or {}
                w.writerow({"rank": p["rank"], "promo_id": p["promo_id"], "source": p["source"], "segments": seg_str(p),
                            "duration_s": p["duration_s"], "hook": p.get("hook", ""), "cliffhanger": p.get("cliffhanger", ""),
                            "why": p.get("why", ""), "final_score": p["final_score"], "ai_hook": s.get("hook"),
                            "ai_cliffhanger": s.get("cliffhanger"), "ai_standalone": s.get("standalone"),
                            "judge_hook": j.get("hook"), "judge_cliffhanger": j.get("cliffhanger"),
                            "judge_standalone": j.get("standalone"), "judge_note": j.get("note"),
                            "rule_total": p["rule_scores"]["total"], "spoiler_risk": j.get("spoiler_risk") or p.get("spoiler_risk", ""),
                            "also_found_by_rules": p.get("also_found_by_rules", ""), "flags": "; ".join(p.get("flags", []))})

        # sheet for a human editor to grade the top picks (the real quality check)
        with open(out / "review_sheet.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["rank", "promo_id", "segments", "hook", "cliffhanger", "would_post(y/n)", "hook_ok(y/n)",
                        "cliffhanger_ok(y/n)", "standalone_ok(y/n)", "timing_fix", "notes"])
            for p in ranked[:20]:
                w.writerow([p["rank"], p["promo_id"], seg_str(p), p.get("hook", ""), p.get("cliffhanger", ""), "", "", "", "", "", ""])

        ai = [p for p in ranked if p["source"] == "ai"]
        meta = {
            "show": show, "episodes": len(eps), "model": (f"{self.llm.provider}:{'+'.join(self.llm.used_models)}" if self.llm and self.llm.used_models
                      else (self.llm.name if self.llm else "rules-only (no AI)")),
            "runtime_s": round(secs, 1), "promos": len(ranked),
            "by_source": {s: sum(1 for p in ranked if p["source"] == s) for s in ("rules", "ai", "ai-montage")},
            "rejected": len(rejected),
            "repaired_by_code": sum(1 for p in ranked if p.get("flags")),
            "ai_rules_agreement": round(sum(1 for p in ai if p.get("also_found_by_rules")) / len(ai), 2) if ai else None,
            "llm_calls": self.llm.calls if self.llm else 0,
            "llm_calls_served_from_cache": self.llm.cache_hits if self.llm else 0,
            "models_used": self.llm.used_models if self.llm else [],
            "quota_exhausted": self.llm.exhausted if self.llm else [],
            "ai_calls_failed": sum(1 for r in rejected if "LLM error" in str(r.get("reason", ""))),
            "approx_tokens_in": (self.llm.chars_in // 4) if self.llm else 0,
            "approx_tokens_out": (self.llm.chars_out // 4) if self.llm else 0,
        }
        (out / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # human-readable report
        md = [f"# Promo picks - {show}", "",
              f"Model: `{meta['model']}` | episodes: {meta['episodes']} | promos: {meta['promos']} "
              f"(rules {meta['by_source']['rules']}, AI {meta['by_source']['ai']}, montages {meta['by_source']['ai-montage']}) "
              f"| rejected: {meta['rejected']} | repaired by code: {meta['repaired_by_code']}", "",
              "## Post first (top 10)", "",
              "| # | Segments | Len | Score | Hook | Cliffhanger |", "|---|---|---|---|---|---|"]
        for p in ranked[:10]:
            md.append(f"| {p['rank']} | {seg_str(p)} | {p['duration_s']:.0f}s | {p['final_score']} | "
                      f"{str(p.get('hook', '')).replace('|', '/')[:90]} | {str(p.get('cliffhanger', '')).replace('|', '/')[:90]} |")
        md += ["", "## Details (top 15)", ""]
        for p in ranked[:15]:
            j = p.get("judge") or {}
            md += [f"### {p['rank']}. {p.get('title', p['promo_id'])}  ({p['source']}, {p['duration_s']:.0f}s, score {p['final_score']})",
                   f"- **Segments:** {seg_str(p)}",
                   f"- **Hook:** {p.get('hook', '')}",
                   f"- **Cliffhanger:** {p.get('cliffhanger', '')}",
                   f"- **Why:** {p.get('why', '')}"]
            if j:
                md.append(f"- **Blind judge:** hook {j.get('hook')}, cliffhanger {j.get('cliffhanger')}, standalone {j.get('standalone')} - {j.get('note')}")
            if p.get("flags"):
                md.append(f"- **Code fixes/flags:** {'; '.join(p['flags'])}")
            md += ["", "```", viewer_script(p, eps, kinds=("action", "dialogue", "sound")), "```", ""]
        if rejected:
            md += ["## Rejected by validation", ""] + [f"- {r.get('source')} E{r.get('episode', '?')}: {r['reason']}" for r in rejected[:40]]
        (out / "report.md").write_text("\n".join(md), encoding="utf-8")
        return meta

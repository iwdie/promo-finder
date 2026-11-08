"""Plain-code rules: legal cut points, validation/repair of promos, and a
no-AI heuristic scorer used (a) as a baseline and (b) as a second opinion on
AI picks.

Design rule: the AI is never trusted with timestamps. Every segment it
proposes is snapped to a real camera cut where no line of dialogue is
running, and the 20-60s length is enforced here, not in the prompt.
"""
from __future__ import annotations

import re
from .parser import Episode, Event, fmt

MIN_LEN, MAX_LEN, HOOK_WINDOW = 20.0, 60.0, 3.0
SNAP_TOL = 3.0  # seconds a proposed timestamp may move to reach a clean cut

# --- keyword lists (deliberately small and readable; tune per genre) -------
PHYSICAL = r"slap|strike|struck|punch|kiss|gasp|scream|collaps|blood|knife|dagger|sword|blade|grab|seiz|shove|crash|" \
           r"shatter|slam|throw|hurl|chok|poison|arsenic|faint|one knee|kneel|gun|burn|stab|wound|bleed|drag|unmask|" \
           r"rip|tear[s]? (?:open|off)|guards? (?:seize|grab)|fall|plunge|smash|whip|attack|lunge|pin[s]? her"
STRONG_WORDS = r"marry|married|wife|husband|divorce|pregnan|child|dead|die|kill|murder|poison|liar|lie|betray|never|" \
               r"how dare|guards|arrest|duke|duchess|secret|truth|love|hate|reveng|ruin|worthless|disgrace|mistress|" \
               r"heir|title|blood|traitor|prison|hang|exile|cheat|steal|stole|enough|kneel|beg|get out|who are you"
INTENSE_DELIVERY = r"furious|scream|shout|shock|horrif|enrag|desperat|menac|gloat|venom|outrag|sob|crying|roar|threat|" \
                   r"cold|cruel|livid|panick|terrif|accus|icy|deadly|explod|seething|hiss"
CLIFF_SOUND = r"cliffhanger|sting|drone|swell|chord|ominous|thud|crash|boom|silence|heartbeat"
REPLY_STARTERS = r"^(yes|no|and|but|so|because|then|well|oh|why\??|what\??|really\??|of course)\b"
DULL_CAMERA = r"establishing|wide shot|aerial|exterior|drone shot|landscape"

_rx = lambda p: re.compile(p, re.I)
PHYSICAL_RX, STRONG_RX, INTENSE_RX = _rx(PHYSICAL), _rx(STRONG_WORDS), _rx(INTENSE_DELIVERY)
CLIFF_RX, REPLY_RX, DULL_RX = _rx(CLIFF_SOUND), _rx(REPLY_STARTERS), _rx(DULL_CAMERA)


# --- cut points ------------------------------------------------------------
def cut_points(ep: Episode) -> list[float]:
    """Times where an editor can cleanly cut: camera-shot boundaries and scene
    boundaries, excluding any moment where a dialogue line is being spoken."""
    pts = {0.0, round(ep.duration, 2)}
    for e in ep.events:
        if e.kind == "camera":
            pts.update({round(e.start, 2), round(e.end, 2)})
    for s in ep.scenes:
        pts.update({round(s["start"], 2), round(s["end"], 2)})
    talking = [(e.start, e.end) for e in ep.events if e.kind == "dialogue"]
    ok = [t for t in sorted(pts) if not any(a + 0.05 < t < b - 0.05 for a, b in talking)]
    return ok


def snap(t: float, cuts: list[float], tol: float = SNAP_TOL, prefer: str = "near") -> float | None:
    """Move t to a clean cut. prefer='before' for starts (never skip the hook line),
    'after' for ends (never chop the last line); falls back to the nearest cut."""
    if prefer == "before":
        c = [x for x in cuts if t - tol <= x <= t + 0.05]
        if c:
            return max(c)
    elif prefer == "after":
        c = [x for x in cuts if t - 0.05 <= x <= t + tol]
        if c:
            return min(c)
    best = min(cuts, key=lambda c: abs(c - t)) if cuts else None
    return best if best is not None and abs(best - t) <= tol else None


# --- per-window features -----------------------------------------------------
def events_in(ep: Episode, a: float, b: float) -> list[Event]:
    return [e for e in ep.events if e.start >= a - 0.05 and e.end <= b + 0.05]


def _hits(rx, text: str) -> int:
    return len(rx.findall(text or ""))


def hook_score(ep: Episode, s: float) -> tuple[float, str]:
    """0-10: how hard do the first 3 seconds grab a scroller?"""
    first = [e for e in ep.events if s - 0.05 <= e.start < s + HOOK_WINDOW]
    score, beat = 0.0, ""
    lines = [e for e in first if e.kind == "dialogue"]
    visual = [e for e in first if e.kind in ("action", "camera", "sound")]
    if lines:
        score += 2.5
        ln = lines[0]
        beat = f'{ln.speaker}: "{ln.text}"'
        if re.search(r"[!?]", ln.text): score += 1
        score += min(2.0, 1.0 * _hits(STRONG_RX, ln.text))
        if INTENSE_RX.search(ln.delivery): score += 1
    phys = sum(_hits(PHYSICAL_RX, e.text) for e in visual)
    if phys:
        score += min(4.0, 2.0 * phys)
        beat = beat or next(e.text for e in visual if PHYSICAL_RX.search(e.text))
    if not lines and not phys:
        score -= 1.5 if any(DULL_RX.search(e.text) for e in visual) else 0.5
        beat = beat or (visual[0].text if visual else "(nothing happens)")
    return max(0.0, min(10.0, score * 1.25)), beat


def cliff_score(ep: Episode, s: float, e: float) -> tuple[float, str]:
    """0-10: does it stop on an unresolved beat?"""
    tail = [x for x in ep.events if e - 4.0 <= x.end <= e + 0.05 and x.start >= s]
    score, beat = 0.0, ""
    lines = [x for x in tail if x.kind == "dialogue"]
    if lines:
        last = lines[-1]
        beat = f'{last.speaker}: "{last.text}"'
        if last.text.rstrip().endswith("?"): score += 2.5
        elif "!" in last.text or last.text.rstrip().endswith("..."): score += 1.5
        score += min(2.0, _hits(STRONG_RX, last.text))
        if INTENSE_RX.search(last.delivery): score += 0.5
    snd = [x for x in tail if x.kind == "sound" and CLIFF_RX.search(x.text)]
    if snd:
        score += 2.5
        beat = beat or snd[-1].text
    score += min(2.0, sum(_hits(PHYSICAL_RX, x.text) for x in tail if x.kind != "dialogue"))
    # an answer is coming right after the cut -> the viewer is left hanging
    nxt = [x for x in ep.events if x.kind == "dialogue" and e - 0.05 <= x.start <= e + 2.5]
    if lines and nxt and nxt[0].speaker != lines[-1].speaker: score += 1.5
    if e >= ep.duration - 3.0: score += 2.0      # writers put the episode cliffhanger here
    if not beat and tail: beat = tail[-1].text
    return max(0.0, min(10.0, score * 1.1)), beat


def standalone_score(ep: Episode, s: float, e: float) -> float:
    """0-10: rough guess at whether a stranger can follow it."""
    score = 6.0
    scene_starts = [sc["start"] for sc in ep.scenes]
    if any(abs(s - t) <= 1.0 for t in scene_starts): score += 2.0
    first_line = next((x for x in ep.events if x.kind == "dialogue" and x.start >= s - 0.05), None)
    if first_line and first_line.start < s + 6 and REPLY_RX.search(first_line.text): score -= 2.5
    if any(s + 1 < t < e - 1 for t in scene_starts): score -= 1.5   # jumps location mid-clip
    speakers = {x.speaker for x in events_in(ep, s, e) if x.kind == "dialogue"}
    if len(speakers) > 4: score -= 1.0
    return max(0.0, min(10.0, score))


def intensity_score(ep: Episode, s: float, e: float) -> float:
    ev = events_in(ep, s, e)
    n = sum(1 for x in ev if PHYSICAL_RX.search(x.text) or (x.kind == "dialogue" and (INTENSE_RX.search(x.delivery) or STRONG_RX.search(x.text))))
    return max(0.0, min(10.0, 10.0 * n / max(1.0, (e - s) / 3.0)))


def heuristic_scores(ep: Episode, s: float, e: float) -> dict:
    h, hook_beat = hook_score(ep, s)
    c, cliff_beat = cliff_score(ep, s, e)
    st = standalone_score(ep, s, e)
    it = intensity_score(ep, s, e)
    dur = e - s
    total = 0.35 * h + 0.35 * c + 0.2 * st + 0.1 * it + (0.3 if 25 <= dur <= 45 else 0.0)
    return {"hook": round(h, 1), "cliffhanger": round(c, 1), "standalone": round(st, 1),
            "intensity": round(it, 1), "total": round(total, 2), "hook_beat": hook_beat, "cliff_beat": cliff_beat}


# --- validation ------------------------------------------------------------
def validate_promo(promo: dict, eps: dict[int, Episode], cuts: dict[int, list[float]]) -> tuple[dict | None, str]:
    """Snap every segment to clean cuts and enforce 20-60s total.
    Returns (fixed_promo, "") or (None, reason). Adds 'flags' for anything repaired."""
    flags, segs = [], []
    for seg in promo.get("segments", []):
        n = int(seg["episode"])
        if n not in eps:
            return None, f"episode {n} not in input"
        ep, cp = eps[n], cuts[n]
        try:
            s0, e0 = float(seg["start_s"]), float(seg["end_s"])
        except (KeyError, ValueError):
            return None, "unreadable timestamp"
        s, e = snap(s0, cp, prefer="before"), snap(e0, cp, prefer="after")
        if s is None or e is None:
            return None, f"E{n} {fmt(s0)}-{fmt(e0)}: no clean cut within {SNAP_TOL}s"
        if abs(s - s0) > 0.3 or abs(e - e0) > 0.3:
            flags.append(f"snapped E{n} {fmt(s0)}-{fmt(e0)} -> {fmt(s)}-{fmt(e)}")
        if e <= s:
            return None, f"E{n}: end before start"
        segs.append({"episode": n, "start_s": s, "end_s": e})
    if not segs:
        return None, "no segments"

    total = sum(x["end_s"] - x["start_s"] for x in segs)
    if total > MAX_LEN:
        # trim the START of the last segment's lead-in is risky; trim from the end of the
        # longest segment instead but keep its last line intact by using cut points.
        last = max(segs, key=lambda x: x["end_s"] - x["start_s"])
        over = total - MAX_LEN
        options = [c for c in cuts[last["episode"]] if last["start_s"] + 3 < c <= last["end_s"] - over]
        if not options:
            return None, f"too long ({total:.1f}s) and could not trim"
        flags.append(f"trimmed E{last['episode']} end {fmt(last['end_s'])} -> {fmt(max(options))} (was {total:.1f}s)")
        last["end_s"] = max(options)
    elif total < MIN_LEN:
        last = segs[-1]
        need = MIN_LEN - total
        options = [c for c in cuts[last["episode"]] if c >= last["end_s"] + need]
        if not options or options[0] - last["end_s"] > need + 15:
            return None, f"too short ({total:.1f}s) and could not extend"
        flags.append(f"extended E{last['episode']} end {fmt(last['end_s'])} -> {fmt(options[0])} (was {total:.1f}s)")
        last["end_s"] = options[0]

    total = sum(x["end_s"] - x["start_s"] for x in segs)
    if not (MIN_LEN - 0.01 <= total <= MAX_LEN + 0.01):
        return None, f"length {total:.1f}s outside {MIN_LEN:.0f}-{MAX_LEN:.0f}s after repair"
    # overlapping segments inside one promo
    for a in segs:
        for b in segs:
            if a is not b and a["episode"] == b["episode"] and a["start_s"] < b["end_s"] and b["start_s"] < a["end_s"]:
                return None, "segments overlap"
    for a, b in zip(segs, segs[1:]):
        if a["episode"] > b["episode"] or (a["episode"] == b["episode"] and b["start_s"] < a["start_s"]):
            flags.append("segments play out of story order")
            break
    first = segs[0]
    beats =[x for x in eps[first["episode"]].events
             if x.kind != "camera" and first["start_s"] - 0.05 <= x.start < first["start_s"] + HOOK_WINDOW]
    if not beats:
        flags.append("no dialogue/action in first 3s (hook is visual only)")

    out = dict(promo)
    out["segments"] = [{**x, "start": fmt(x["start_s"]), "end": fmt(x["end_s"])} for x in segs]
    out["duration_s"] = round(total, 2)
    out["flags"] = flags
    return out, ""


# --- no-AI baseline ----------------------------------------------------------
def overlap(a: dict, b: dict) -> float:
    """Fraction of the shorter promo that overlaps the other (same-episode time)."""
    inter = 0.0
    for x in a["segments"]:
        for y in b["segments"]:
            if x["episode"] == y["episode"]:
                inter += max(0.0, min(x["end_s"], y["end_s"]) - max(x["start_s"], y["start_s"]))
    return inter / max(1e-6, min(a["duration_s"], b["duration_s"]))


def baseline_candidates(ep: Episode, cuts: list[float], per_episode: int = 3) -> list[dict]:
    """Slide over every clean (start, end) cut pair of 20-60s, score with rules,
    keep the best non-overlapping windows."""
    scored = []
    for s in cuts:
        for e in cuts:
            if MIN_LEN <= e - s <= MAX_LEN:
                hs = heuristic_scores(ep, s, e)
                scored.append((hs["total"], s, e, hs))
    scored.sort(key=lambda x: -x[0])
    picked: list[dict] = []
    for tot, s, e, hs in scored:
        cand = {"source": "rules", "segments": [{"episode": ep.number, "start_s": s, "end_s": e}], "duration_s": e - s}
        if all(overlap(cand, p) < 0.3 for p in picked):
            cand.update({"title": f"E{ep.number} @ {fmt(s)}", "hook": hs["hook_beat"], "cliffhanger": hs["cliff_beat"],
                         "why": "rule score: strong opening beat + unresolved ending", "rule_scores": hs})
            picked.append(cand)
        if len(picked) >= per_episode:
            break
    return picked

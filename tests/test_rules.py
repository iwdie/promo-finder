"""Run: python -m pytest -q   (uses only the synthetic sample, no Kuku data)"""
from pathlib import Path

from promo_finder.parser import load_show, parse_time, fmt
from promo_finder.rules import cut_points, validate_promo, baseline_candidates, snap
from promo_finder.llm import extract_json

SAMPLE = Path(__file__).parent.parent / "examples" / "sample_show"


def _ep():
    eps = load_show(SAMPLE)
    return eps[0], {1: eps[0]}, {1: cut_points(eps[0])}


def test_parse():
    ep, _, _ = _ep()
    assert ep.number == 1 and ep.duration == 65.0
    assert {e.kind for e in ep.events} == {"camera", "action", "dialogue", "sound"}
    assert parse_time("01:05.50") == 65.5 and fmt(65.5) == "01:05.50"


def test_no_cut_inside_dialogue():
    ep, _, cuts = _ep()
    for t in cuts[1]:
        assert not any(d.start + 0.05 < t < d.end - 0.05 for d in ep.events if d.kind == "dialogue")


def test_snap_direction():
    cuts = [0.0, 4.0, 7.0]
    assert snap(5.0, cuts, prefer="before") == 4.0   # starts move earlier (keep the hook line)
    assert snap(5.0, cuts, prefer="after") == 7.0    # ends move later (keep the last line)


def test_validate_trims_and_rejects():
    _, eps, cuts = _ep()
    ok, why = validate_promo({"segments": [{"episode": 1, "start_s": 4.0, "end_s": 58.0}]}, eps, cuts)
    assert ok and 20 <= ok["duration_s"] <= 60
    too_long, _ = validate_promo({"segments": [{"episode": 1, "start_s": 0.0, "end_s": 65.0}]}, eps, cuts)
    assert too_long is None or too_long["duration_s"] <= 60
    bad, why = validate_promo({"segments": [{"episode": 9, "start_s": 0, "end_s": 30}]}, eps, cuts)
    assert bad is None and "episode 9" in why


def test_baseline_finds_the_slap():
    ep, _, cuts = _ep()
    best = baseline_candidates(ep, cuts[1], 1)[0]
    assert best["segments"][0]["start_s"] <= 4.0   # opens on/just before the slap


def test_extract_json_handles_chatter():
    assert extract_json('Sure!\n```json\n{"a": "x \\" }", "b": 2}\n```') == {"a": 'x " }', "b": 2}

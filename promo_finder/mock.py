"""Fake model for offline tests: exercises the whole AI code path without a key.
Its picks are deliberately naive - never use mock output as real results."""
import json
import re

LINE = re.compile(r"^#(\d+) \[(\d+):(\d+\.\d+)-(\d+):(\d+\.\d+)\] (\w[^:]*):(.*)$", re.M)


def mock_complete(system: str, user: str) -> str:
    if "TASK: EPISODE" in user:
        rows = [(int(i), int(a) * 60 + float(b), int(c) * 60 + float(d), who, txt) for i, a, b, c, d, who, txt in LINE.findall(user)]
        promos, moments = [], []
        starts = [r for r in rows if r[3] not in ("CAMERA", "ACTION", "SOUND") and re.search(r"[!?]", r[4])]
        for r in starts[:: max(1, len(starts) // 2 or 1)][:2]:
            end = next((x for x in rows if x[2] >= r[1] + 32), rows[-1])
            promos.append({"title": f"mock {r[0]}", "segments": [{"from_id": r[0], "to_id": end[0]}],
                           "hook": r[4].strip()[:80], "cliffhanger": end[4].strip()[:80], "why": "mock",
                           "scores": {"hook": 6, "cliffhanger": 6, "standalone": 6}, "spoiler_risk": "low"})
            moments.append({"from_id": r[0], "to_id": r[0] + 3, "type": "hook", "summary": r[4].strip()[:60]})
        promos.append({"title": "mock bad ids", "segments": [{"from_id": 99999, "to_id": 100000}]})
        return json.dumps({"promos": promos, "key_moments": moments})
    if "TASK: MONTAGE" in user:
        ids = re.findall(r"^(M\d+_\d+):", user, re.M)
        out = [{"title": "mock montage", "moment_ids": ids[i:i + 3], "hook": "mock", "cliffhanger": "mock", "why": "mock",
                "scores": {"hook": 6, "cliffhanger": 6, "standalone": 5}, "spoiler_risk": "medium"} for i in (0, 6, 12) if len(ids) > i + 2]
        return "Sure! Here you go:\n```json\n" + json.dumps({"montages": out}) + "\n```"
    if "TASK: JUDGE" in user:
        n = len(re.findall(r"^### P\d+", user, re.M))
        return json.dumps({"ratings": [{"id": f"P{i}", "hook": 5, "cliffhanger": 5, "standalone": 5,
                                        "spoiler_risk": "low", "note": "mock"} for i in range(1, n + 1)]})
    return "{}"

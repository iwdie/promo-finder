"""Parse Kuku-style microdrama screenplays into timestamped events.

Each episode file looks like:
    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. CATHEDRAL – DAY
    Scene start: 00:00.00
    Scene end: 02:18.00
    **CAMERA:**            /  **ACTION:**  /  **SOUND FX:**  /  **NAME (Delivery):**
    Start: (00:00.00) | End: (00:04.80)
    text...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

TIME_RE = r"(\d+):(\d{2}(?:\.\d+)?)"
BLOCK_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
SPAN_RE = re.compile(rf"^Start:\s*\({TIME_RE}\)\s*\|\s*End:\s*\({TIME_RE}\)")
NON_SPEAKERS = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sound", "SOUND": "sound", "SFX": "sound"}


def to_sec(m: str, s: str) -> float:
    return int(m) * 60 + float(s)


def fmt(t: float) -> str:
    """Seconds -> 'mm:ss.ss' (same style as the screenplays)."""
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def parse_time(s: str) -> float:
    m = re.fullmatch(r"\s*" + TIME_RE + r"\s*", str(s))
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    return to_sec(m.group(1), m.group(2))


@dataclass
class Event:
    idx: int
    kind: str          # camera | action | dialogue | sound
    start: float
    end: float
    text: str
    scene: int
    speaker: str = ""
    delivery: str = ""

    def line(self) -> str:
        """Compact one-line form used in prompts and reports."""
        span = f"[{fmt(self.start)}-{fmt(self.end)}]"
        if self.kind == "dialogue":
            d = f" ({self.delivery})" if self.delivery else ""
            return f"{span} {self.speaker}{d}: {self.text}"
        return f"{span} {self.kind.upper()}: {self.text}"


@dataclass
class Episode:
    number: int
    title: str
    logline: str
    continuity: str
    events: list[Event] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)
    source: str = ""

    @property
    def duration(self) -> float:
        ends = [e.end for e in self.events] + [s["end"] for s in self.scenes]
        return max(ends) if ends else 0.0

    def transcript(self, kinds=("camera", "action", "dialogue", "sound")) -> str:
        return "\n".join(e.line() for e in self.events if e.kind in kinds)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        return d


def _split_speaker(label: str) -> tuple[str, str]:
    # "DETECTIVE MIA (Calm, Firm)" -> ("DETECTIVE MIA", "Calm, Firm")
    # "MATRIARCH (O.S.) (Firm)" -> ("MATRIARCH (O.S.)", "Firm")
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", label)
    if m and m.group(2).upper() not in {"O.S.", "V.O.", "CONT'D"}:
        return m.group(1).strip(), m.group(2).strip()
    return label.strip(), ""


def parse_episode(path: str | Path) -> Episode:
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header = {}
    for ln in lines[:10]:
        if ":" in ln and ln.split(":", 1)[0].isupper():
            k, v = ln.split(":", 1)
            header[k.strip()] = v.strip()
    num_m = re.search(r"(\d+)", header.get("EPISODE NUMBER", "")) or re.search(r"episode-(\d+)", path.name)
    ep = Episode(
        number=int(num_m.group(1)) if num_m else 0,
        title=header.get("EPISODE NUMBER", path.stem),
        logline=header.get("EPISODE LOGLINE", ""),
        continuity=header.get("CONTINUITY NOTES", ""),
        source=path.name,
    )

    scene_no, i = 0, 0
    while i < len(lines):
        ln = lines[i].strip()
        if re.match(r"^SCENE\s+\d+", ln):
            scene_no = int(re.findall(r"\d+", ln)[0])
            ep.scenes.append({"scene": scene_no, "heading": "", "start": 0.0, "end": 0.0})
        elif ln.startswith("Scene heading:") and ep.scenes:
            ep.scenes[-1]["heading"] = ln.split(":", 1)[1].strip()
        elif ln.startswith("Scene start:") and ep.scenes:
            ep.scenes[-1]["start"] = parse_time(ln.split(":", 1)[1])
        elif ln.startswith("Scene end:") and ep.scenes:
            ep.scenes[-1]["end"] = parse_time(ln.split(":", 1)[1])
        else:
            bm = BLOCK_RE.match(ln)
            if bm and i + 1 < len(lines):
                sm = SPAN_RE.match(lines[i + 1].strip())
                if sm:
                    label = bm.group(1).strip()
                    start, end = to_sec(sm.group(1), sm.group(2)), to_sec(sm.group(3), sm.group(4))
                    j, body = i + 2, []
                    while j < len(lines) and lines[j].strip() and not BLOCK_RE.match(lines[j].strip()):
                        body.append(lines[j].strip())
                        j += 1
                    text = " ".join(body).strip()
                    kind = NON_SPEAKERS.get(label.upper())
                    if kind:
                        ev = Event(len(ep.events), kind, start, end, text, scene_no)
                    else:
                        spk, deliv = _split_speaker(label)
                        ev = Event(len(ep.events), "dialogue", start, end, text.strip('"“” '), scene_no, spk, deliv)
                    ep.events.append(ev)
                    i = j
                    continue
        i += 1
    ep.events.sort(key=lambda e: (e.start, e.end))
    for k, e in enumerate(ep.events):
        e.idx = k
    return ep


def load_show(folder: str | Path) -> list[Episode]:
    """Load every *.txt screenplay under a folder (recursively), sorted by episode number."""
    files = sorted(Path(folder).rglob("*.txt"))
    eps = [parse_episode(f) for f in files]
    eps = [e for e in eps if e.events]
    return sorted(eps, key=lambda e: e.number)

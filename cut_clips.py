#!/usr/bin/env python3
"""Turn ranked promos into something an editor can use.

With episode videos:   cut every segment with ffmpeg and join them -> clips/P001.mp4, P002.mp4 ...
Without videos:        write cut_list.csv + cut_commands.sh (ffmpeg commands with the video names to fill in)

Examples
  python cut_clips.py --promos outputs/ai/promos.json --out clips --top 10
  python cut_clips.py --promos outputs/ai/promos.json --videos path/to/episodes --out clips --top 10
Video files are matched to episodes by the number in their name (ep01.mp4, Episode 1.mp4, 001.mp4 ...).
Cuts are re-encoded (not stream-copied) so they land on the exact frame, not the nearest keyframe.
"""
import argparse
import csv
import json
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def match_videos(folder: Path) -> dict[int, Path]:
    out = {}
    for f in sorted(folder.rglob("*")):
        if f.suffix.lower() in VIDEO_EXT:
            m = re.search(r"(?:ep(?:isode)?)[ _-]*0*(\d+)", f.stem, re.I) or re.search(r"(\d+)", f.stem)
            if m:
                out.setdefault(int(m.group(1)), f)
    return out


def run(cmd: list[str]):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-400:]}")


def cut_promo(promo: dict, videos: dict[int, Path], out: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, s in enumerate(promo["segments"]):
            part = Path(tmp) / f"part{i}.mp4"
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s['start_s']:.2f}", "-to", f"{s['end_s']:.2f}",
                 "-i", str(videos[s["episode"]]), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(part)])
            parts.append(part)
        dest = out / f"{promo['promo_id']}.mp4"
        if len(parts) == 1:
            shutil.move(str(parts[0]), dest)
        else:
            lst = Path(tmp) / "list.txt"
            lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(dest)])
        return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--promos", required=True, help="promos.json from find_promos.py")
    ap.add_argument("--videos", help="folder with the episode video files (optional)")
    ap.add_argument("--out", default="clips")
    ap.add_argument("--top", type=int, default=10, help="how many top-ranked promos")
    a = ap.parse_args()

    promos = json.load(open(a.promos, encoding="utf-8"))[: a.top]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1) always: an editor-readable cut list and a ready-to-run ffmpeg script
    with open(out / "cut_list.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "promo_id", "part", "episode", "start", "end", "seconds", "hook", "cliffhanger"])
        for p in promos:
            for i, s in enumerate(p["segments"], 1):
                w.writerow([p["rank"], p["promo_id"], i, s["episode"], s["start"], s["end"],
                            round(s["end_s"] - s["start_s"], 2), p.get("hook", ""), p.get("cliffhanger", "")])
    lines = ["#!/bin/sh", "# Replace EPISODE_<n>.mp4 with your episode files, then run: sh cut_commands.sh"]
    for p in promos:
        parts = []
        for i, s in enumerate(p["segments"], 1):
            part = f"{p['promo_id']}_part{i}.mp4"
            parts.append(part)
            lines.append(f"ffmpeg -y -ss {s['start_s']:.2f} -to {s['end_s']:.2f} -i EPISODE_{s['episode']}.mp4 "
                         f"-c:v libx264 -preset veryfast -crf 20 -c:a aac {part}")
        if len(parts) == 1:
            lines.append(f"mv {parts[0]} {p['promo_id']}.mp4")
        else:
            lines.append("printf \"" + "".join(f"file '{x}'\\n" for x in parts) + f"\" > {p['promo_id']}_list.txt")
            lines.append(f"ffmpeg -y -f concat -safe 0 -i {p['promo_id']}_list.txt -c copy {p['promo_id']}.mp4")
    (out / "cut_commands.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out / 'cut_list.csv'} and {out / 'cut_commands.sh'} for {len(promos)} promos")

    # 2) if videos are available: cut them for real
    if a.videos:
        if not shutil.which("ffmpeg"):
            raise SystemExit("ffmpeg not found - install it from https://ffmpeg.org (or use cut_commands.sh later)")
        videos = match_videos(Path(a.videos))
        print(f"Found videos for episodes: {sorted(videos)}")
        for p in promos:
            missing = sorted({s["episode"] for s in p["segments"]} - set(videos))
            if missing:
                print(f"  skip {p['promo_id']}: no video for episode(s) {missing}")
                continue
            dest = cut_promo(p, videos, out)
            print(f"  {p['promo_id']} -> {dest} ({p['duration_s']:.1f}s)")


if __name__ == "__main__":
    main()

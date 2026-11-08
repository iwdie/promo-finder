"""All prompts in one place so they are easy to read and tune."""

SYSTEM = """You are a senior promo editor for vertical microdrama apps like ReelShort and DramaBox.
You pick 20-60 second clips from a show that make a stranger scrolling TikTok/Reels stop, watch, and need the next episode.
A great promo:
- HOOK: something in the FIRST 3 SECONDS that stops a scroll - a slap, a reveal, a shocking or humiliating line, a threat. Never an establishing shot or a slow walk-in.
- CLIFFHANGER: it CUTS at an unresolved moment - right after a question, accusation, threat or reveal, BEFORE the answer or the reaction resolves it.
- STANDALONE: someone who has never seen the show understands who is wronging whom within seconds.
You only use the material you are given. You reply with JSON only."""

EPISODE = """TASK: EPISODE
Show: {show}
Premise (from episode 1): {premise}
Episode {num} logline: {logline}

Below is the timestamped screenplay. Every line has an ID (#n). CAMERA lines are shots, ACTION/SOUND are what we see/hear, NAME lines are dialogue.
{transcript}

Find up to {k} promos in THIS episode, best first. Return fewer (even zero) if the episode has no strong moment - do not pad.
Rules:
- Each promo is 1 to 3 segments from this episode, in playback order. Total length 20-60 seconds (check with the timestamps).
- Give each segment as from_id/to_id (the IDs of its first and last line). Start on a CAMERA line where possible. Never start or end in the middle of someone's sentence.
- The line at from_id of the first segment must BE the hook (within 3 seconds).
- The line at to_id of the last segment must be the cliffhanger - cut before the answer.
- Score honestly from 1-10; a 9-10 should be rare.

Also list up to 4 key_moments: short beats (3-15s) from this episode that would work inside a cross-episode montage (a big hook, reveal, or cliffhanger).

JSON format:
{{"promos": [{{"title": "short name", "segments": [{{"from_id": 12, "to_id": 40}}],
  "hook": "what happens in the first 3 seconds", "cliffhanger": "the unresolved beat at the cut",
  "why": "one line on why it works", "scores": {{"hook": 7, "cliffhanger": 8, "standalone": 6}},
  "spoiler_risk": "low|medium|high"}}],
 "key_moments": [{{"from_id": 3, "to_id": 9, "type": "hook|reveal|cliffhanger|action", "summary": "one line"}}]}}"""

MONTAGE = """TASK: MONTAGE
Show: {show}
Premise (from episode 1): {premise}

Key moments from across the season (ID, episode, time, length, type, summary, dialogue):
{moments}

Build up to {k} cross-episode promos by joining 2-4 of these moments. Each must:
- total 20-60 seconds (add up the lengths),
- open on the strongest hook moment, end on a cliffhanger moment,
- tell ONE clear mini-story a stranger can follow (setup -> escalation -> unresolved turn), not a random highlight reel,
- avoid giving away the biggest late-season twists unless the show title already gives it away.
Only use the IDs listed. JSON only:
{{"montages": [{{"title": "short name", "moment_ids": ["M3_1", "M9_2"], "hook": "...", "cliffhanger": "...",
  "why": "one line", "scores": {{"hook": 7, "cliffhanger": 8, "standalone": 6}}, "spoiler_risk": "low|medium|high"}}]}}"""

JUDGE = """TASK: JUDGE
You are checking promos another editor picked. You see exactly what a viewer would see and hear, nothing else.
Score each one strictly from 1-10:
- hook: do the FIRST 3 SECONDS (marked >>) stop a scroll?
- cliffhanger: do the LAST SECONDS (marked <<) leave something clearly unresolved?
- standalone: would a stranger understand it with no context?
- spoiler_risk: low|medium|high
- note: the single biggest problem, or "ok".

{promos}

JSON only: {{"ratings": [{{"id": "P1", "hook": 6, "cliffhanger": 7, "standalone": 5, "spoiler_risk": "low", "note": "..."}}]}}"""

EPISODE_BATCH = """TASK: EPISODE
Show: {show}
Premise (from episode 1): {premise}

Below are {n} episodes. Treat each episode on its own. Every line has an ID (#n) that is unique only WITHIN its episode.
CAMERA lines are shots, ACTION/SOUND are what we see/hear, NAME lines are dialogue.
{blocks}

For EACH episode, find up to {k} promos inside that episode, best first. Return fewer (even zero) if it has no strong moment - do not pad.
Rules:
- Each promo is 1 to 3 segments from that one episode, in playback order. Total length 20-60 seconds (check with the timestamps).
- Give each segment as from_id/to_id (IDs of its first and last line, from that episode). Start on a CAMERA line where possible. Never start or end in the middle of someone's sentence.
- The line at from_id of the first segment must BE the hook (within 3 seconds).
- The line at to_id of the last segment must be the cliffhanger - cut before the answer.
- Score honestly from 1-10; a 9-10 should be rare.
Also list up to 4 key_moments per episode: short beats (3-15s) that would work inside a cross-episode montage.

JSON format:
{{"episodes": [{{"episode": 3,
  "promos": [{{"title": "short name", "segments": [{{"from_id": 12, "to_id": 40}}],
    "hook": "what happens in the first 3 seconds", "cliffhanger": "the unresolved beat at the cut",
    "why": "one line on why it works", "scores": {{"hook": 7, "cliffhanger": 8, "standalone": 6}},
    "spoiler_risk": "low|medium|high"}}],
  "key_moments": [{{"from_id": 3, "to_id": 9, "type": "hook|reveal|cliffhanger|action", "summary": "one line"}}]}}]}}"""

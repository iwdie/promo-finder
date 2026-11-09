"""One tiny interface for every model: complete(system, user) -> text.

Swap models with two settings, no code changes:
    --provider gemini     --model gemini-3.5-flash      (GEMINI_API_KEY)
    --provider groq       --model llama-3.3-70b-versatile (GROQ_API_KEY)
    --provider openai     --model gpt-4o-mini           (OPENAI_API_KEY, OPENAI_BASE_URL optional)
    --provider anthropic  --model claude-sonnet-4-5     (ANTHROPIC_API_KEY)
    --provider openrouter / ollama / any OpenAI-compatible endpoint via --base-url
    --provider mock       (offline, for tests only — fake answers)
Plain HTTP via `requests`, so there is no SDK lock-in.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import requests

OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", ""),
}
# free-tier models tried in order when one runs out of daily quota
FALLBACKS = {
    "gemini": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemma-4-31b-it"],
}
DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "anthropic": "claude-sonnet-4-5",
    "ollama": "llama3.1",
    "mock": "mock",
}


class LLM:
    def __init__(self, provider: str = "gemini", model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, temperature: float = 0.4, cache_dir: str | None = ".cache",
                 min_interval: float = 0.0, max_retries: int = 5, fallback_models: list[str] | None = None):
        self.provider = provider.lower()
        self.model = model or DEFAULT_MODELS.get(self.provider, "")
        # when a model's daily quota is used up, move to the next one (each has its own free quota)
        chain = [self.model] + list(FALLBACKS.get(self.provider, []) if fallback_models is None else fallback_models)
        self.models = list(dict.fromkeys(m for m in chain if m))
        self.exhausted: list[str] = []
        self.quota_events: list[str] = []
        self.used_models: list[str] = []
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.cache = Path(cache_dir) if cache_dir else None
        self.min_interval = min_interval      # seconds between calls (free tiers are rate limited)
        self.max_retries = max_retries
        self._last = 0.0
        self._lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0
        self.chars_in = 0
        self.chars_out = 0
        if self.provider == "mock":
            from .mock import mock_complete
            self._mock = mock_complete

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    # ---------------------------------------------------------------------
    def complete(self, system: str, user: str) -> str:
        key = hashlib.sha256(f"{self.name}|{self.temperature}|{system}|{user}".encode()).hexdigest()[:24]
        if self.cache:
            f = self.cache / f"{key}.txt"
            if f.exists():                       # cached reply: count it so cost stats stay true
                text = f.read_text(encoding="utf-8")
                self.calls += 1
                self.cache_hits += 1
                self.chars_in += len(system) + len(user)
                self.chars_out += len(text)
                return text
        with self._lock:                      # pace calls across threads
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
        attempt = 0
        while True:
            if self.model in self.exhausted:
                if not self._next_model():
                    raise QuotaExhausted(f"daily quota used up for: {', '.join(self.exhausted)}")
                continue
            try:
                text = self._call(system, user)
                if self.model not in self.used_models:
                    self.used_models.append(self.model)
                break
            except QuotaExhausted as e:
                self.exhausted.append(self.model)
                self.quota_events.append(f"{self.model}: {e}")
                print(f"  [llm] {self.model}: daily quota used up")
            except RetryableError as e:
                attempt += 1
                if attempt >= self.max_retries:
                    raise RuntimeError(f"{self.name}: gave up after {self.max_retries} retries ({e})")
                delay = e.retry_after or min(60, 5 * 2 ** attempt)
                print(f"  [llm] {e} -> retry in {delay:.0f}s")
                time.sleep(delay)
        self.calls += 1
        self.chars_in += len(system) + len(user)
        self.chars_out += len(text)
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)
            (self.cache / f"{key}.txt").write_text(text, encoding="utf-8")
        return text

    def _next_model(self) -> bool:
        for m in self.models:
            if m not in self.exhausted:
                print(f"  [llm] switching to {m}")
                self.model = m
                return True
        return False

    def complete_json(self, system: str, user: str) -> tuple[dict, str]:
        """Returns (parsed_json, raw_text). One repair retry if the JSON is broken."""
        raw = self.complete(system, user)
        try:
            return extract_json(raw), raw
        except ValueError:
            raw2 = self.complete(system, user + "\n\nYour previous answer was not valid JSON. Reply with ONLY the JSON object.")
            return extract_json(raw2), raw2

    # ---------------------------------------------------------------------
    def _call(self, system: str, user: str) -> str:
        p = self.provider
        if p == "mock":
            return self._mock(system, user)
        if p == "gemini":
            key = self.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": self.temperature, "responseMimeType": "application/json"},
            }
            r = _post(url, body, {"x-goog-api-key": key or ""})
            try:
                return "".join(part.get("text", "") for part in r["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError):
                raise RuntimeError(f"gemini: unexpected response {json.dumps(r)[:300]}")
        if p == "anthropic":
            key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            body = {"model": self.model, "max_tokens": 4096, "temperature": self.temperature, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            r = _post("https://api.anthropic.com/v1/messages", body,
                      {"x-api-key": key, "anthropic-version": "2023-06-01"})
            return "".join(b.get("text", "") for b in r.get("content", []))
        # everything else: OpenAI-compatible chat completions
        base, env = OPENAI_COMPATIBLE.get(p, (None, "OPENAI_API_KEY"))
        base = self.base_url or (os.environ.get("OPENAI_BASE_URL") if p == "openai" else None) or base
        base = base or "https://api.openai.com/v1"
        key = self.api_key or (os.environ.get(env, "") if env else "")
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if p in ("openai", "groq"):
            body["response_format"] = {"type": "json_object"}
        r = _post(base.rstrip("/") + "/chat/completions", body, {"Authorization": f"Bearer {key}"} if key else {})
        return r["choices"][0]["message"]["content"]


class QuotaExhausted(Exception):
    """The provider's daily/free quota is used up for this model."""


class RetryableError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


def _post(url: str, body: dict, headers: dict) -> dict:
    try:
        r = requests.post(url, json=body, headers={"Content-Type": "application/json", **headers}, timeout=600)
    except requests.RequestException as e:
        raise RetryableError(f"network error: {e}")
    if r.status_code == 429:
        text = r.text
        m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', text)
        wait = float(m.group(1)) if m else None
        ra = r.headers.get("retry-after")
        if wait is None and ra and ra.replace(".", "", 1).isdigit():
            wait = float(ra)
        # a per-day limit (or a wait longer than 2 minutes) will not clear by retrying
        if re.search(r"per ?day|PerDay|daily", text, re.I) or (wait or 0) > 120:
            raise QuotaExhausted("daily limit reached")
        raise RetryableError("HTTP 429 (rate limit)", wait)
    if r.status_code in (500, 502, 503, 504):
        raise RetryableError(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    raise ValueError("no JSON object found in model reply")

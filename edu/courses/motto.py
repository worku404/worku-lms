import json
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

GEMINI_URL = "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent"
MOTTO_CACHE_PREFIX = "daily_motto"
FAILURE_TTL_SECONDS = 60 * 15


def _seconds_until_midnight():
    now = timezone.localtime()
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(60, int((tomorrow - now).total_seconds()))


def _extract_json(text):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            return json.loads(snippet)
        raise


def _normalize_motto(payload):
    text = str(payload.get("text", "")).strip()
    author = str(payload.get("author", "")).strip() or "Unknown"
    source = str(payload.get("source", "")).strip()
    if not text:
        text = "Daily motto unavailable."
        author = "Unknown"
        source = ""
    return {
        "text": text,
        "author": author,
        "source": source,
    }
    

import random

# High-signal domains
philosophy = [
    "ancient philosophy (Stoicism, Aristotle, Plato)",
    "modern philosophy (existentialism, Nietzsche, Camus)",
    "ethics and moral philosophy",
    "logic and rational thinking",
]

religion_spirituality = [
    "Orthodox Christianity teachings",
    "Biblical wisdom and teachings",
    "early Church Fathers",
    "spiritual discipline and asceticism",
]

literature = [
    "classic literature wisdom",
    "Russian literature (Dostoevsky, Tolstoy)",
    "poetry and human nature",
    "themes of suffering and meaning in literature",
]

# Cognitive domains
psychology = [
    "human behavior and psychology",
    "cognitive biases and decision making",
    "emotional intelligence",
    "habit formation and discipline",
]

self_mastery = [
    "discipline and self-control",
    "focus and deep work",
    "resilience and perseverance",
    "personal responsibility and growth",
]

# Strategic domains
strategy = [
    "strategic thinking and decision making",
    "military strategy (Sun Tzu, Clausewitz)",
    "power dynamics and leadership",
    "long-term thinking and planning",
]

topics = [
    philosophy,
    religion_spirituality,
    literature,
    psychology,
    self_mastery,
    strategy,
]

def _build_prompt():
    daily_topic = random.choice(topics)      # pick category
    daily_quote = random.choice(daily_topic) # pick specific topic

    return (
        f"Return ONE real, short quote about {daily_quote}. "
        "Reply ONLY with valid JSON (no markdown, no commentary) in this exact shape: "
        "{\"text\":\"...\",\"author\":\"...\",\"source\":\"...\"}. "
        "Keep the quote under 240 characters. Ensure the author is a real person. "
        "If uncertain about the source, set \"source\" to \"Unknown\"."
    )

def _call_gemini():
    api_keys = [
        settings.API1_KEY,
        settings.API2_KEY,
        settings.API3_KEY,
        settings.API4_KEY,
    ]
    api_keys = [key.strip() for key in api_keys if isinstance(key, str) and key.strip()]
    if not api_keys:
        raise RuntimeError(
            "No Gemini API keys are configured. Add API1_KEY..API4_KEY in your .env file."
        )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _build_prompt()}],
            }
        ]
    }

    last_error = None
    for key_index, api_key in enumerate(api_keys, start=1):
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        try:
            response = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=60)
            if response.status_code == 200:
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text
            last_error = {
                "key_index": key_index,
                "status": response.status_code,
                "message": response.text[:200],
            }
        except requests.RequestException as exc:
            last_error = {"key_index": key_index, "error": str(exc)}
    raise RuntimeError(f"All Gemini API keys failed. Last error: {last_error}")


def get_daily_motto(force_refresh=False):
    today = timezone.localdate().isoformat()
    cache_key = f"{MOTTO_CACHE_PREFIX}:{today}"
    cached = cache.get(cache_key)
    if cached and not force_refresh:
        return cached

    try:
        response_text = _call_gemini()
        raw_payload = _extract_json(response_text)
        motto = _normalize_motto(raw_payload)
        cache.set(cache_key, motto, _seconds_until_midnight())
        return motto
    except Exception:
        if cached:
            return cached
        placeholder = _normalize_motto({})
        cache.set(cache_key, placeholder, FAILURE_TTL_SECONDS)
        return placeholder

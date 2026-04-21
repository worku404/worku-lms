import json
import re
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

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

classic_jokes = [
    "dad jokes",
    "knock-knock jokes",
    "lightbulb jokes",
    "bar jokes (e.g., a horse walks into a bar)"
]

wordplay_jokes = [
    "terrible but hilarious puns",
    "food based puns",
    "animal puns",
    "grammar and spelling jokes"
]

situational_jokes = [
    "computer programmer and tech support jokes",
    "office and workplace jokes",
    "school and teacher jokes",
    "doctor and hospital jokes"
]

quick_laughs = [
    "funny one-liners",
    "clever two-line jokes",
    "witty anti-jokes",
    "funny riddles with unexpected answers"
]

topics = [
    classic_jokes,
    wordplay_jokes,
    situational_jokes,
    quick_laughs,
]

def _build_prompt():
    category = random.choice(topics)
    topic = random.choice(category)

    return (
        f"Return ONE very funny {topic}. "
        "Reply ONLY with valid JSON (no markdown, no commentary): "
        "{\"text\":\"...\",\"author\":\"...\",\"source\":\"...\"}. "
        "Keep it under 240 characters. "
        "Author can be the comedian's name or 'Unknown'. "
        "If unsure, set source to \"Unknown\"."
    )
    
def _call_gemini():
    from assistant.services import generate_ai_response

    return generate_ai_response({"prompt": _build_prompt()}, system_prompt="")


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

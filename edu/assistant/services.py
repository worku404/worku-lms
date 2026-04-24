from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests
from django.conf import settings


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_SECONDS = 120


class GeminiError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


def _get_gemini_api_keys() -> list[str]:
    api_keys = [
        getattr(settings, "API1_KEY", None),
        getattr(settings, "API2_KEY", None),
        getattr(settings, "API3_KEY", None),
        getattr(settings, "API4_KEY", None),
    ]
    return [
        key.strip()
        for key in api_keys
        if isinstance(key, str) and key.strip()
    ]


def _build_contents(input_context: dict[str, Any]) -> list[dict[str, Any]]:
    if "contents" in input_context and isinstance(input_context["contents"], list):
        return input_context["contents"]

    messages = input_context.get("messages")
    if isinstance(messages, list):
        contents: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = (message.get("role") or "user").strip() or "user"
            text = (message.get("text") or "").strip()
            if not text:
                continue
            contents.append({"role": role, "parts": [{"text": text}]})
        if contents:
            return contents

    prompt = (input_context.get("prompt") or "").strip()
    if prompt:
        return [{"role": "user", "parts": [{"text": prompt}]}]

    raise ValueError(
        "input_context must include 'contents', 'messages', or 'prompt'."
    )


def _inject_system_prompt(
    contents: list[dict[str, Any]],
    system_prompt: str,
) -> list[dict[str, Any]]:
    system_prompt = (system_prompt or "").strip()
    if not system_prompt:
        return list(contents)

    # The public Generative Language `v1` API used by this project does not accept
    # a `systemInstruction` field, so we prepend the instruction as the first
    # message to keep compatibility across environments.
    system_text = (
        "System instruction:\n"
        f"{system_prompt}\n\n"
        "Follow the system instruction above."
    )
    return [{"role": "user", "parts": [{"text": system_text}]}] + list(contents)


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return ""
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    content = first.get("content") if isinstance(first, dict) else {}
    parts = (content or {}).get("parts") if isinstance(content, dict) else []
    if not isinstance(parts, list) or not parts:
        return ""
    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_text = part.get("text")
        if part_text:
            text_parts.append(str(part_text))
    return "".join(text_parts)


def _extract_error_message(response: requests.Response) -> str | None:
    error_payload: Any = {}
    try:
        error_payload = response.json()
    except ValueError:
        error_payload = {"message": response.text[:300]}

    api_error = (
        error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
    )
    if isinstance(api_error, dict):
        return api_error.get("message") or error_payload.get("message")
    return error_payload.get("message") if isinstance(error_payload, dict) else None


def _extract_prompt_feedback_message(payload: dict[str, Any]) -> str | None:
    prompt_feedback = payload.get("promptFeedback")
    if not isinstance(prompt_feedback, dict):
        return None

    block_reason = str(prompt_feedback.get("blockReason") or "").strip()
    if not block_reason:
        return None

    return f"Gemini blocked the prompt ({block_reason})."


def _iter_sse_payloads(response: requests.Response) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []

    def flush_event() -> Iterator[dict[str, Any]]:
        nonlocal data_lines
        if not data_lines:
            return iter(())

        payload_text = "\n".join(data_lines).strip()
        data_lines = []
        if not payload_text or payload_text == "[DONE]":
            return iter(())

        return iter((json.loads(payload_text),))

    for raw_line in response.iter_lines(decode_unicode=True, chunk_size=1):
        if raw_line is None:
            continue
        line = str(raw_line).rstrip("\r")
        if not line:
            yield from flush_event()
            continue
        if line.startswith(":") or line.startswith("event:") or line.startswith("id:"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        else:
            data_lines.append(line)

    yield from flush_event()


def stream_ai_response(input_context: dict[str, Any], system_prompt: str) -> Iterator[str]:
    api_keys = _get_gemini_api_keys()
    if not api_keys:
        raise GeminiError(
            "No Gemini API keys are configured. Add API1_KEY..API4_KEY in your .env file."
        )

    model_name = (input_context.get("model") or DEFAULT_GEMINI_MODEL).strip()
    timeout = int(input_context.get("timeout") or DEFAULT_TIMEOUT_SECONDS)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:streamGenerateContent?alt=sse"
    )

    contents = _build_contents(input_context)
    contents = _inject_system_prompt(contents, system_prompt)

    payload: dict[str, Any] = {"contents": contents}

    generation_config: dict[str, Any] = {}
    for key in ("temperature", "topP", "topK", "maxOutputTokens"):
        if key in input_context and input_context[key] is not None:
            generation_config[key] = input_context[key]
    if generation_config:
        payload["generationConfig"] = generation_config

    last_error: dict[str, Any] | None = None

    for key_index, api_key in enumerate(api_keys, start=1):
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        response: requests.Response | None = None
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            last_error = {
                "key_index": key_index,
                "error": str(exc),
                "type": exc.__class__.__name__,
            }
            continue

        try:
            if response.status_code == 200:
                generated_any = False
                previous_chunk_text = ""
                prompt_feedback_message = None
                try:
                    for payload_event in _iter_sse_payloads(response):
                        if not isinstance(payload_event, dict):
                            continue
                        if prompt_feedback_message is None:
                            prompt_feedback_message = _extract_prompt_feedback_message(
                                payload_event
                            )
                        chunk_text = _extract_text(payload_event)
                        if not chunk_text:
                            continue
                        generated_any = True
                        if previous_chunk_text and chunk_text.startswith(previous_chunk_text):
                            delta = chunk_text[len(previous_chunk_text) :]
                        else:
                            delta = chunk_text
                        previous_chunk_text = chunk_text
                        if delta:
                            yield delta
                except ValueError as exc:
                    raise GeminiError(
                        f"Failed to decode Gemini stream: {exc}",
                        details={"key_index": key_index, "status": 200},
                    ) from exc
                except requests.RequestException as exc:
                    raise GeminiError(
                        f"Network error while contacting Gemini: {exc}",
                        details={
                            "key_index": key_index,
                            "status": 200,
                            "type": exc.__class__.__name__,
                        },
                    ) from exc

                if not generated_any:
                    if prompt_feedback_message:
                        raise GeminiError(
                            prompt_feedback_message,
                            details={
                                "key_index": key_index,
                                "status": 400,
                                "message": prompt_feedback_message,
                            },
                        )
                    raise GeminiError("Gemini returned an empty response.")
                return

            message = _extract_error_message(response)
            last_error = {
                "key_index": key_index,
                "status": response.status_code,
                "message": message,
            }

            if response.status_code in (400, 503, 504):
                break
        finally:
            response.close()

    raise GeminiError(_build_failure_message(last_error), details=last_error)


def _build_failure_message(last_error: dict[str, Any] | None) -> str:
    if not last_error:
        return "All API keys failed."

    if last_error.get("error") and not last_error.get("status"):
        return f"Network error while contacting Gemini: {last_error['error']}"

    status = last_error.get("status")
    message = str(last_error.get("message") or "").strip()
    lowered_message = message.lower()

    if status == 429 and any(
        fragment in lowered_message
        for fragment in (
            "high demand",
            "temporarily unavailable",
            "overloaded",
            "spikes in demand",
        )
    ):
        return "Gemini is temporarily overloaded. Please try again in a moment."

    if status == 429:
        return "All API keys are over quota right now."

    if status in (401, 403):
        return (
            "All API keys were rejected (invalid key, API disabled, or key restriction mismatch)."
        )

    if status == 400 and any(
        fragment in lowered_message
        for fragment in (
            "token",
            "context",
            "too large",
            "prompt too long",
            "request size",
        )
    ):
        return (
            "The prompt is too long for the current model. Split it into smaller parts and try again."
        )

    if status in (503, 504) or any(
        fragment in lowered_message
        for fragment in (
            "high demand",
            "temporarily unavailable",
            "overloaded",
        )
    ):
        return "Gemini is temporarily overloaded. Please try again in a moment."

    return "All API keys failed."


def generate_ai_response(input_context: dict[str, Any], system_prompt: str) -> str:
    """
    Shared Gemini client used across apps.

    Args:
        input_context: dict containing either:
            - "contents": Gemini REST contents list, OR
            - "messages": [{"role": "user"|"model", "text": "..."}], OR
            - "prompt": "...".
        system_prompt: system instruction string.
    """

    api_keys = _get_gemini_api_keys()
    if not api_keys:
        raise GeminiError(
            "No Gemini API keys are configured. Add API1_KEY..API4_KEY in your .env file."
        )

    model_name = (input_context.get("model") or DEFAULT_GEMINI_MODEL).strip()
    timeout = int(input_context.get("timeout") or DEFAULT_TIMEOUT_SECONDS)

    url = (
        "https://generativelanguage.googleapis.com/v1/models/"
        f"{model_name}:generateContent"
    )

    contents = _build_contents(input_context)
    contents = _inject_system_prompt(contents, system_prompt)

    payload: dict[str, Any] = {"contents": contents}

    generation_config: dict[str, Any] = {}
    for key in ("temperature", "topP", "topK", "maxOutputTokens"):
        if key in input_context and input_context[key] is not None:
            generation_config[key] = input_context[key]
    if generation_config:
        payload["generationConfig"] = generation_config

    last_error: dict[str, Any] | None = None

    for key_index, api_key in enumerate(api_keys, start=1):
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = {
                "key_index": key_index,
                "error": str(exc),
                "type": exc.__class__.__name__,
            }
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                data = {}
            return _extract_text(data)

        message = _extract_error_message(response)

        last_error = {
            "key_index": key_index,
            "status": response.status_code,
            "message": message,
        }

        if response.status_code in (400, 503, 504):
            break

    raise GeminiError(_build_failure_message(last_error), details=last_error)

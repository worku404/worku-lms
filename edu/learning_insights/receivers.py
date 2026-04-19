from __future__ import annotations

from django.dispatch import receiver
from students.signals import (
    content_progress_recorded,
    module_time_tracked,
    presence_ping_recorded,
)

from learning_insights.services.tracking import (
    safe_record_content_progress_event,
    safe_record_module_time_event,
    safe_record_presence_ping,
)


@receiver(module_time_tracked)
def handle_module_time_tracked(sender, **kwargs):
    user = kwargs.get("user")
    module = kwargs.get("module")
    seconds_delta = kwargs.get("seconds_delta", 0)
    recorded_at = kwargs.get("recorded_at")

    if user is None or module is None:
        return

    safe_record_module_time_event(
        user=user,
        module=module,
        seconds_delta=seconds_delta,
        recorded_at=recorded_at,
    )


@receiver(content_progress_recorded)
def handle_content_progress_recorded(sender, **kwargs):
    user = kwargs.get("user")
    content = kwargs.get("content")
    seconds_delta = kwargs.get("seconds_delta", 0)
    completed_now = kwargs.get("completed_now", False)
    recorded_at = kwargs.get("recorded_at")

    if user is None or content is None:
        return

    safe_record_content_progress_event(
        user=user,
        content=content,
        seconds_delta=seconds_delta,
        completed_now=completed_now,
        recorded_at=recorded_at,
    )


@receiver(presence_ping_recorded)
def handle_presence_ping_recorded(sender, **kwargs):
    user_id = kwargs.get("user_id")
    recorded_at = kwargs.get("recorded_at")

    if not user_id:
        return

    safe_record_presence_ping(
        user_id=user_id,
        recorded_at=recorded_at,
    )

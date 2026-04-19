from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from django.contrib.auth import get_user_model
from django.utils import timezone

from learning_insights.models import Goal, InsightNotification, NotificationPreference
from learning_insights.services.analytics import build_weekly_summary
from learning_insights.services.common import (
    get_local_now,
    get_or_create_notification_preference,
    get_period_end,
    get_period_start,
)
from learning_insights.services.goals import sync_goal_progress


def _notification_exists(user, dedupe_key: str) -> bool:
    if not dedupe_key:
        return False
    return InsightNotification.objects.filter(user=user, dedupe_key=dedupe_key).exists()


def _create_notification(
    *,
    user,
    category: str,
    title: str,
    body: str,
    dedupe_key: str,
    scheduled_for=None,
    payload: dict | None = None,
) -> InsightNotification | None:
    if _notification_exists(user, dedupe_key):
        return None

    return InsightNotification.objects.create(
        user=user,
        category=category,
        channel=InsightNotification.CHANNEL_IN_APP,
        title=title[:200],
        body=body[:1000],
        dedupe_key=(dedupe_key or "")[:120],
        scheduled_for=scheduled_for or timezone.now(),
        payload=payload or {},
    )


def _minute_label(total_minutes: int) -> str:
    if total_minutes <= 0:
        return "0 minutes"
    if total_minutes == 1:
        return "1 minute"
    return f"{total_minutes} minutes"


def _hour_label(total_seconds: int) -> str:
    hours = round(float(total_seconds) / 3600.0, 1)
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def _goal_due_today_notifications(
    user, preference, local_now
) -> list[InsightNotification]:
    notifications: list[InsightNotification] = []
    today = local_now.date()

    due_goals = Goal.objects.filter(
        user=user,
        due_date=today,
    ).exclude(status=Goal.STATUS_COMPLETED)

    for goal in due_goals:
        dedupe_key = f"goal-due:{goal.id}:{today.isoformat()}"
        body = f'Your goal "{goal.title}" is due today.'
        if goal.course_id and goal.course:
            body = f'Your goal "{goal.title}" for {goal.course.title} is due today.'

        notification = _create_notification(
            user=user,
            category=InsightNotification.CATEGORY_GOAL_DUE,
            title="Goal due today",
            body=body,
            dedupe_key=dedupe_key,
            scheduled_for=timezone.now(),
            payload={
                "goal_id": goal.id,
                "course_id": goal.course_id,
                "url": goal.get_absolute_url()
                if hasattr(goal, "get_absolute_url")
                else "",
            },
        )
        if notification is not None:
            notifications.append(notification)

    return notifications


def _daily_start_notification(
    user, preference, local_now
) -> InsightNotification | None:
    if not preference.in_app_enabled or not preference.daily_enabled:
        return None

    reminder_time = preference.daily_time
    if local_now.time() < reminder_time:
        return None

    today = local_now.date()
    dedupe_key = f"daily-start:{today.isoformat()}"

    active_goals = Goal.objects.filter(
        user=user,
        start_date__lte=today,
        due_date__gte=today,
        period_type=Goal.PERIOD_DAILY,
    ).exclude(status=Goal.STATUS_COMPLETED)

    goal_count = active_goals.count()
    if goal_count > 0:
        body = f"Start your day strong. You have {goal_count} daily goal(s) in focus."
    else:
        body = "Start your day strong. No daily goals are active yet, so this is a good time to create one."

    return _create_notification(
        user=user,
        category=InsightNotification.CATEGORY_DAILY_START,
        title="Your daily learning plan is ready",
        body=body,
        dedupe_key=dedupe_key,
        scheduled_for=timezone.now(),
        payload={
            "goal_count": goal_count,
        },
    )


def _weekly_start_notification(
    user, preference, local_now
) -> InsightNotification | None:
    if not preference.in_app_enabled or not preference.weekly_enabled:
        return None

    today = local_now.date()
    if local_now.weekday() != int(preference.week_start_day):
        return None

    reminder_time = preference.weekly_time
    if local_now.time() < reminder_time:
        return None

    week_start = get_period_start(
        today,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )
    week_end = get_period_end(
        today,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )
    dedupe_key = f"weekly-start:{week_start.isoformat()}"

    weekly_goals = Goal.objects.filter(
        user=user,
        start_date__lte=week_end,
        due_date__gte=week_start,
        period_type=Goal.PERIOD_WEEKLY,
    ).exclude(status=Goal.STATUS_COMPLETED)

    goal_count = weekly_goals.count()
    if goal_count > 0:
        body = (
            f"Your new learning week has started. "
            f"You have {goal_count} weekly goal(s) ready for this period."
        )
    else:
        body = "Your new learning week has started. Set a weekly goal to track planned versus actual progress."

    return _create_notification(
        user=user,
        category=InsightNotification.CATEGORY_WEEKLY_START,
        title="Your weekly learning plan is ready",
        body=body,
        dedupe_key=dedupe_key,
        scheduled_for=timezone.now(),
        payload={
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "goal_count": goal_count,
        },
    )


def _daily_achievement_notification(
    user, preference, local_now
) -> InsightNotification | None:
    if not preference.in_app_enabled or not preference.daily_achievement_enabled:
        return None

    yesterday = local_now.date() - timedelta(days=1)
    dedupe_key = f"daily-achievement:{yesterday.isoformat()}"

    summary = build_weekly_summary(
        user,
        preference=preference,
        week_start=yesterday,
    )
    daily_breakdown = summary.get("daily_breakdown") or []
    day_summary = next(
        (item for item in daily_breakdown if item.get("date") == yesterday), None
    )
    if not day_summary:
        return None

    site_minutes = int(day_summary.get("site_active_minutes", 0) or 0)
    study_minutes = int(day_summary.get("course_minutes", 0) or 0)
    completed_goals = int(day_summary.get("completed_goals", 0) or 0)
    completed_contents = int(day_summary.get("completed_contents", 0) or 0)

    if (
        site_minutes <= 0
        and study_minutes <= 0
        and completed_goals <= 0
        and completed_contents <= 0
    ):
        return None

    parts: list[str] = []
    if site_minutes > 0:
        parts.append(f"{_minute_label(site_minutes)} active on site")
    if study_minutes > 0:
        parts.append(f"{_minute_label(study_minutes)} of course study")
    if completed_goals > 0:
        parts.append(f"{completed_goals} completed goal(s)")
    if completed_contents > 0:
        parts.append(f"{completed_contents} completed content item(s)")

    body = "Yesterday you recorded " + ", ".join(parts) + "."

    return _create_notification(
        user=user,
        category=InsightNotification.CATEGORY_DAILY_ACHIEVEMENT,
        title="Your daily achievement summary",
        body=body,
        dedupe_key=dedupe_key,
        scheduled_for=timezone.now(),
        payload={
            "date": yesterday.isoformat(),
            "site_minutes": site_minutes,
            "study_minutes": study_minutes,
            "completed_goals": completed_goals,
            "completed_contents": completed_contents,
        },
    )


def _weekly_achievement_notification(
    user, preference, local_now
) -> InsightNotification | None:
    if not preference.in_app_enabled or not preference.weekly_achievement_enabled:
        return None

    today = local_now.date()
    if local_now.weekday() != int(preference.week_start_day):
        return None
    if local_now.time() < preference.weekly_time:
        return None

    previous_reference = today - timedelta(days=1)
    summary = build_weekly_summary(
        user,
        preference=preference,
        week_start=previous_reference,
    )

    period = summary.get("period") or {}
    week_start = period.get("start") or get_period_start(
        previous_reference,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )
    week_end = period.get("end") or get_period_end(
        previous_reference,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )

    dedupe_key = f"weekly-achievement:{week_start.isoformat()}"

    total_site_seconds = int(summary.get("total_site_seconds", 0) or 0)
    achievement_percent = round(float(summary.get("achievement_percent", 0) or 0), 1)
    status = summary.get("weekly_status") or {}
    status_label = status.get("label") or "No goals set"
    top_courses = summary.get("top_courses") or []

    if total_site_seconds <= 0 and achievement_percent <= 0 and not top_courses:
        return None

    parts = [
        f"{_hour_label(total_site_seconds)} active on site",
        f"{achievement_percent}% achievement",
        f"status: {status_label}",
    ]

    if top_courses:
        top_course = top_courses[0]
        top_course_title = (
            top_course.get("course_title")
            or top_course.get("title")
            or "your top course"
        )
        parts.append(f"top course: {top_course_title}")

    body = "Last week summary: " + ", ".join(parts) + "."

    return _create_notification(
        user=user,
        category=InsightNotification.CATEGORY_WEEKLY_ACHIEVEMENT,
        title="Your weekly achievement summary",
        body=body,
        dedupe_key=dedupe_key,
        scheduled_for=timezone.now(),
        payload={
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "achievement_percent": achievement_percent,
            "status_label": status_label,
            "total_site_seconds": total_site_seconds,
        },
    )


def ensure_due_notifications(user) -> list[InsightNotification]:
    if not getattr(user, "is_authenticated", False):
        return []

    preference = get_or_create_notification_preference(user)
    local_now = get_local_now(preference=preference)
    sync_goal_progress(user, reference_date=local_now.date())

    created: list[InsightNotification] = []

    for item in (
        _daily_start_notification(user, preference, local_now),
        _weekly_start_notification(user, preference, local_now),
        _daily_achievement_notification(user, preference, local_now),
        _weekly_achievement_notification(user, preference, local_now),
    ):
        if item is not None:
            created.append(item)

    created.extend(_goal_due_today_notifications(user, preference, local_now))
    return created


def get_unread_notifications(
    user, limit: int | None = None
) -> Iterable[InsightNotification]:
    queryset = InsightNotification.objects.filter(
        user=user,
        channel=InsightNotification.CHANNEL_IN_APP,
        read_at__isnull=True,
        dismissed_at__isnull=True,
    ).order_by("-scheduled_for", "-created")

    if limit is not None:
        return queryset[:limit]
    return queryset


def get_notification_payload(
    user,
    *,
    limit: int = 4,
    mark_read: bool = False,
) -> list[dict]:
    ensure_due_notifications(user)

    notifications = list(get_unread_notifications(user, limit=limit))
    payload = [
        {
            "id": item.id,
            "title": item.title,
            "body": item.body,
            "category": item.category,
            "created": item.created.isoformat(),
            "scheduled_for": item.scheduled_for.isoformat()
            if item.scheduled_for
            else None,
            "url": item.get_target_url(),
        }
        for item in notifications
    ]

    if mark_read and notifications:
        now = timezone.now()
        unread_ids = [item.id for item in notifications if item.read_at is None]
        if unread_ids:
            InsightNotification.objects.filter(id__in=unread_ids).update(read_at=now)

    return payload


def mark_notification_read(notification: InsightNotification) -> InsightNotification:
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return notification


def dismiss_notification(notification: InsightNotification) -> InsightNotification:
    now = timezone.now()
    changed_fields: list[str] = []

    if notification.read_at is None:
        notification.read_at = now
        changed_fields.append("read_at")

    if notification.dismissed_at is None:
        notification.dismissed_at = now
        changed_fields.append("dismissed_at")

    if changed_fields:
        notification.save(update_fields=changed_fields)

    return notification


def mark_notifications_read(notifications: Iterable[InsightNotification]) -> int:
    ids = [item.id for item in notifications if item.read_at is None]
    if not ids:
        return 0
    return InsightNotification.objects.filter(id__in=ids).update(read_at=timezone.now())


def generate_notifications_for_all_users() -> int:
    User = get_user_model()
    total_created = 0

    for user in User.objects.filter(is_active=True):
        if not getattr(user, "is_authenticated", True):
            continue
        total_created += len(ensure_due_notifications(user))

    return total_created

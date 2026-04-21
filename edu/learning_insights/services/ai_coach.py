from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from assistant.services import GeminiError, generate_ai_response
from courses.models import Course
from django.db import transaction
from django.utils import timezone
from notes.models import Note
from students.models import CourseProgress, ModuleProgress

from learning_insights.models import AIPlanRun, Goal, NotificationPreference
from learning_insights.services.analytics import build_monthly_summary, build_weekly_summary
from learning_insights.services.common import get_local_date, get_period_end, get_period_start


def _json_default(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, dict):
            return loaded
        return {"value": loaded}
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            return json.loads(snippet)
        raise


def _strip_html(html: str, limit: int = 1200) -> str:
    html = (html or "").strip()
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _serialize_goals(goals) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for goal in goals:
        rows.append(
            {
                "id": goal.id,
                "title": goal.title,
                "description": goal.description,
                "period_type": goal.period_type,
                "target_type": goal.target_type,
                "target_value": float(goal.target_value or 0),
                "current_value": float(goal.current_value or 0),
                "start_date": goal.start_date.isoformat() if goal.start_date else "",
                "due_date": goal.due_date.isoformat() if goal.due_date else "",
                "priority": goal.priority,
                "status": goal.status,
                "course_id": goal.course_id,
                "course_title": goal.course.title if goal.course_id and goal.course else "",
            }
        )
    return rows


def _serialize_preferences(preference: NotificationPreference) -> dict[str, Any]:
    return {
        "timezone": preference.timezone,
        "week_start_day": int(preference.week_start_day),
        "daily_enabled": bool(preference.daily_enabled),
        "weekly_enabled": bool(preference.weekly_enabled),
        "daily_achievement_enabled": bool(preference.daily_achievement_enabled),
        "weekly_achievement_enabled": bool(preference.weekly_achievement_enabled),
        "in_app_enabled": bool(preference.in_app_enabled),
        "telegram_enabled": bool(getattr(preference, "telegram_enabled", False)),
        "telegram_daily_summary_enabled": bool(
            getattr(preference, "telegram_daily_summary_enabled", False)
        ),
        "telegram_weekly_review_enabled": bool(
            getattr(preference, "telegram_weekly_review_enabled", True)
        ),
        "telegram_critical_alerts_enabled": bool(
            getattr(preference, "telegram_critical_alerts_enabled", True)
        ),
        "daily_time": preference.daily_time.isoformat() if preference.daily_time else "",
        "weekly_time": preference.weekly_time.isoformat() if preference.weekly_time else "",
    }


def _serialize_courses(user) -> list[dict[str, Any]]:
    courses = (
        user.courses_joined.select_related("subject")
        .prefetch_related("modules")
        .order_by("title")
    )

    progress_by_course_id = {
        row.course_id: row
        for row in CourseProgress.objects.filter(user=user, course__in=courses)
    }

    module_progress = (
        ModuleProgress.objects.filter(user=user, course__in=courses)
        .select_related("module")
        .order_by("module__order")
    )
    progress_by_course_module: dict[int, list[ModuleProgress]] = {}
    for row in module_progress:
        progress_by_course_module.setdefault(row.course_id, []).append(row)

    serialized: list[dict[str, Any]] = []
    for course in courses:
        course_progress = progress_by_course_id.get(course.id)
        module_rows = progress_by_course_module.get(course.id, [])

        completed_count = sum(1 for row in module_rows if row.completed)
        modules = list(course.modules.all())
        total_modules = len(modules)

        next_module = None
        completed_module_ids = {row.module_id for row in module_rows if row.completed}
        for module in modules:
            if module.id not in completed_module_ids:
                next_module = module
                break

        serialized.append(
            {
                "course_id": course.id,
                "title": course.title,
                "subject": course.subject.title if course.subject_id else "",
                "progress_percent": float(getattr(course_progress, "progress_percent", 0) or 0),
                "completed": bool(getattr(course_progress, "completed", False)),
                "total_modules": total_modules,
                "completed_modules": completed_count,
                "next_module": {
                    "module_id": next_module.id,
                    "order": next_module.order,
                    "title": next_module.title,
                }
                if next_module
                else None,
            }
        )
    return serialized


def _serialize_notes(user, limit: int = 5) -> list[dict[str, Any]]:
    # Tags are optional; we match on slug prefixes that align with constants.py.
    qs = (
        Note.objects.filter(user=user, tags__slug__in=["daily-reflection", "weekly-review"])
        .distinct()
        .order_by("-updated_at")[:limit]
    )

    rows: list[dict[str, Any]] = []
    for note in qs:
        rows.append(
            {
                "id": note.id,
                "title": note.title,
                "updated_at": note.updated_at.isoformat() if note.updated_at else "",
                "content": _strip_html(note.content_html),
            }
        )
    return rows


def build_ai_input_payload(
    *,
    user,
    preference: NotificationPreference,
    week_start: date | None = None,
    month_anchor: date | None = None,
) -> dict[str, Any]:
    week_ctx = build_weekly_summary(user, preference=preference, week_start=week_start)
    month_ctx = build_monthly_summary(user, preference=preference, month_anchor=month_anchor)

    goals = Goal.objects.filter(user=user).select_related("course").order_by("-due_date", "-created")[:50]
    goal_rows = _serialize_goals(goals)

    weekly_top = week_ctx.get("top_courses") or []
    weekly_breakdown = week_ctx.get("course_breakdown") or []
    active_course_ids = {
        int(row.get("course_id"))
        for row in weekly_breakdown
        if row.get("course_id") and int(row.get("total_seconds") or 0) > 0
    }

    courses = _serialize_courses(user)
    neglected = [row for row in courses if row.get("course_id") not in active_course_ids]

    return {
        "goals": goal_rows,
        "weekly_analytics": json.loads(json.dumps(week_ctx, default=_json_default)),
        "monthly_analytics": json.loads(json.dumps(month_ctx, default=_json_default)),
        "courses": courses,
        "neglected_courses": neglected[:5],
        "top_courses": weekly_top[:5],
        "preferences": _serialize_preferences(preference),
        "notes": _serialize_notes(user),
    }


WEEKLY_PLAN_SYSTEM_PROMPT = """
You are an AI learning coach. Generate a realistic weekly study plan.

Hard rules:
- Be realistic and avoid over-optimistic scheduling.
- Adjust workload based on user consistency and recent time spent.
- Prioritize weak or neglected courses when appropriate.
- Include buffer/rest time if the user has low consistency or is at risk.
- Do NOT rely on any single input. Combine goals + analytics + course roadmap + constraints.

Return ONLY valid JSON (no markdown, no commentary).

Required JSON shape:
{
  "reasoning_summary": "",
  "inputs_used": {
    "goals": "",
    "analytics": "",
    "roadmap": "",
    "constraints": ""
  },
  "weekly_focus": [
    {"course_id": 0, "course_title": "", "why": ""}
  ],
  "plan": [
    {
      "date": "YYYY-MM-DD",
      "is_buffer_day": false,
      "items": [
        {
          "course_id": 0,
          "course_title": "",
          "minutes": 30,
          "task": "",
          "notes": ""
        }
      ]
    }
  ],
  "risk_level": "low"
}

Where risk_level is one of: "low", "medium", "high".
""".strip()


DAILY_PLAN_SYSTEM_PROMPT = """
You are an AI learning coach. Generate a realistic study plan for a single day.

Hard rules:
- Be realistic and avoid over-optimistic scheduling.
- Adjust workload based on user consistency and recent time spent.
- Prioritize weak or neglected courses when appropriate.
- Do NOT rely on any single input. Combine goals + analytics + course roadmap + constraints.
- Plan ONLY for the provided day (input context contains "day").

Return ONLY valid JSON (no markdown, no commentary).

Required JSON shape:
{
  "reasoning_summary": "",
  "inputs_used": {
    "goals": "",
    "analytics": "",
    "roadmap": "",
    "constraints": ""
  },
  "date": "YYYY-MM-DD",
  "items": [
    {
      "course_id": 0,
      "course_title": "",
      "minutes": 30,
      "task": "",
      "notes": ""
    }
  ],
  "risk_level": "low"
}

Where risk_level is one of: "low", "medium", "high".
""".strip()


REVIEW_SYSTEM_PROMPT = """
You are an AI learning coach. Produce a performance review summary and actionable next steps.

Requirements:
- Include performance gap analysis (planned vs actual) when possible.
- Detect behavior patterns (timing trends, procrastination signals, consistency changes).
- Provide course-level insights (ignored vs improving courses).
- Suggestions must be actionable (behavioral + study strategy), not just descriptive.

Return ONLY valid JSON (no markdown, no commentary).

Required JSON shape:
{
  "summary": "",
  "achievements": [],
  "missed_targets": [],
  "insights": [],
  "suggestions": [],
  "risk_level": "low"
}

Where risk_level is one of: "low", "medium", "high".
""".strip()


IMPROVEMENT_SYSTEM_PROMPT = """
You are an AI learning coach. Generate improvement suggestions focused on behavior and study strategy.

Requirements:
- Focus on 3-7 concrete changes the user can implement this week.
- Tie each suggestion to evidence from goals + analytics + course roadmap.
- Suggest small experiments (timeboxing, focus blocks, review cadence, distraction control).

Return ONLY valid JSON (no markdown, no commentary).

Required JSON shape:
{
  "summary": "",
  "achievements": [],
  "missed_targets": [],
  "insights": [],
  "suggestions": [],
  "risk_level": "low"
}
""".strip()


RECOVERY_SYSTEM_PROMPT = """
You are an AI learning coach. Generate recovery suggestions for a stalled or missed plan.

Requirements:
- Identify the biggest bottlenecks (time, consistency, overload, unclear next steps).
- Propose a recovery plan for the next 3-5 days with realistic workloads.
- Include a buffer day if risk is medium/high.
- Provide actionable steps (not vague motivation).

Return ONLY valid JSON (no markdown, no commentary).

Required JSON shape:
{
  "summary": "",
  "achievements": [],
  "missed_targets": [],
  "insights": [],
  "suggestions": [],
  "risk_level": "high"
}
""".strip()


def _create_ai_run(
    *,
    user,
    kind: str,
    period_type: str,
    period_start: date | None,
    period_end: date | None,
    input_payload: dict[str, Any],
    system_prompt: str,
) -> AIPlanRun:
    run = AIPlanRun.objects.create(
        user=user,
        kind=kind,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        input_payload=input_payload,
        status=AIPlanRun.STATUS_PENDING,
    )

    prompt = (
        "Input context JSON:\n"
        + json.dumps(input_payload, ensure_ascii=False, indent=2, default=_json_default)
    )

    try:
        raw_text = generate_ai_response({"prompt": prompt}, system_prompt)
        output_payload = _extract_json(raw_text)
    except GeminiError as exc:
        run.status = AIPlanRun.STATUS_FAILED
        run.error_message = exc.message
        run.summary_text = exc.message
        run.save(update_fields=["status", "error_message", "summary_text", "updated_at"])
        return run
    except Exception as exc:
        run.status = AIPlanRun.STATUS_FAILED
        run.error_message = str(exc)
        run.summary_text = "AI output could not be parsed."
        run.save(update_fields=["status", "error_message", "summary_text", "updated_at"])
        return run

    summary_text = ""
    if isinstance(output_payload, dict):
        summary_text = str(
            output_payload.get("reasoning_summary")
            or output_payload.get("summary")
            or ""
        ).strip()

    run.status = AIPlanRun.STATUS_SUCCESS
    run.output_payload = output_payload
    run.summary_text = summary_text[:2000]
    run.error_message = ""
    run.save(update_fields=["status", "output_payload", "summary_text", "error_message", "updated_at"])
    return run


def generate_weekly_plan_run(
    *,
    user,
    preference: NotificationPreference,
    reference_date: date | None = None,
) -> AIPlanRun:
    anchor = reference_date or get_local_date(preference=preference)
    plan_start = get_period_start(
        anchor,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )
    plan_end = plan_start + timedelta(days=6)

    # Use the prior 7 days for behavioral analytics to avoid planning against future dates.
    analytics_start = plan_start - timedelta(days=7)
    payload = build_ai_input_payload(
        user=user,
        preference=preference,
        week_start=analytics_start,
        month_anchor=plan_start,
    )
    payload["period"] = {"start": plan_start.isoformat(), "end": plan_end.isoformat()}

    return _create_ai_run(
        user=user,
        kind=AIPlanRun.KIND_WEEKLY_PLAN,
        period_type=Goal.PERIOD_WEEKLY,
        period_start=plan_start,
        period_end=plan_end,
        input_payload=payload,
        system_prompt=WEEKLY_PLAN_SYSTEM_PROMPT,
    )


def generate_daily_plan_run(
    *,
    user,
    preference: NotificationPreference,
    reference_date: date | None = None,
) -> AIPlanRun:
    plan_date = reference_date or get_local_date(preference=preference)

    recent_start = plan_date - timedelta(days=6)
    payload = build_ai_input_payload(
        user=user,
        preference=preference,
        week_start=recent_start,
        month_anchor=plan_date,
    )
    payload["day"] = plan_date.isoformat()

    return _create_ai_run(
        user=user,
        kind=AIPlanRun.KIND_DAILY_PLAN,
        period_type=Goal.PERIOD_DAILY,
        period_start=plan_date,
        period_end=plan_date,
        input_payload=payload,
        system_prompt=DAILY_PLAN_SYSTEM_PROMPT,
    )


def generate_weekly_review_run(
    *,
    user,
    preference: NotificationPreference,
    reference_date: date | None = None,
) -> AIPlanRun:
    today = get_local_date(preference=preference)
    anchor = reference_date or today
    week_start = get_period_start(
        anchor,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )
    payload = build_ai_input_payload(
        user=user,
        preference=preference,
        week_start=week_start,
        month_anchor=anchor,
    )
    payload["review_period"] = "weekly"

    return _create_ai_run(
        user=user,
        kind=AIPlanRun.KIND_WEEKLY_REVIEW,
        period_type=Goal.PERIOD_WEEKLY,
        period_start=week_start,
        period_end=week_start + timedelta(days=6),
        input_payload=payload,
        system_prompt=REVIEW_SYSTEM_PROMPT,
    )


def generate_daily_review_run(
    *,
    user,
    preference: NotificationPreference,
    reference_date: date | None = None,
) -> AIPlanRun:
    anchor = reference_date or get_local_date(preference=preference)
    weekly_analytics_start = anchor - timedelta(days=6)
    payload = build_ai_input_payload(
        user=user,
        preference=preference,
        week_start=weekly_analytics_start,
        month_anchor=anchor,
    )
    payload["review_period"] = "daily"
    payload["day"] = anchor.isoformat()

    return _create_ai_run(
        user=user,
        kind=AIPlanRun.KIND_DAILY_REVIEW,
        period_type=Goal.PERIOD_DAILY,
        period_start=anchor,
        period_end=anchor,
        input_payload=payload,
        system_prompt=REVIEW_SYSTEM_PROMPT,
    )


def generate_improvement_run(
    *,
    user,
    preference: NotificationPreference,
    reference_date: date | None = None,
) -> AIPlanRun:
    today = get_local_date(preference=preference)
    anchor = reference_date or today
    week_start = get_period_start(
        anchor,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )
    payload = build_ai_input_payload(
        user=user,
        preference=preference,
        week_start=week_start,
        month_anchor=anchor,
    )
    payload["intent"] = "improvement_suggestions"

    return _create_ai_run(
        user=user,
        kind=AIPlanRun.KIND_IMPROVEMENT,
        period_type=Goal.PERIOD_WEEKLY,
        period_start=week_start,
        period_end=week_start + timedelta(days=6),
        input_payload=payload,
        system_prompt=IMPROVEMENT_SYSTEM_PROMPT,
    )


def generate_recovery_run(
    *,
    user,
    preference: NotificationPreference,
    reference_date: date | None = None,
) -> AIPlanRun:
    today = get_local_date(preference=preference)
    anchor = reference_date or today
    week_start = get_period_start(
        anchor,
        Goal.PERIOD_WEEKLY,
        week_start_day=preference.week_start_day,
    )
    payload = build_ai_input_payload(
        user=user,
        preference=preference,
        week_start=week_start,
        month_anchor=anchor,
    )
    payload["intent"] = "recovery_suggestions"

    return _create_ai_run(
        user=user,
        kind=AIPlanRun.KIND_RECOVERY,
        period_type=Goal.PERIOD_WEEKLY,
        period_start=week_start,
        period_end=week_start + timedelta(days=6),
        input_payload=payload,
        system_prompt=RECOVERY_SYSTEM_PROMPT,
    )


def apply_weekly_plan_run(*, run: AIPlanRun) -> int:
    """
    Create daily Goal rows from a weekly plan run.

    Returns: number of goals created.
    """

    if run.kind != AIPlanRun.KIND_WEEKLY_PLAN:
        return 0
    if run.status != AIPlanRun.STATUS_SUCCESS:
        return 0

    payload = run.effective_payload
    plan_days = payload.get("plan") if isinstance(payload, dict) else None
    if not isinstance(plan_days, list):
        return 0

    created_count = 0

    with transaction.atomic():
        for day in plan_days:
            if not isinstance(day, dict):
                continue
            day_value = (day.get("date") or "").strip()
            try:
                day_date = datetime.strptime(day_value, "%Y-%m-%d").date()
            except ValueError:
                continue

            items = day.get("items")
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                course_id = item.get("course_id")
                course = None
                if isinstance(course_id, int) and course_id > 0:
                    course = Course.objects.filter(pk=course_id).first()

                minutes = item.get("minutes")
                try:
                    minutes_value = max(0, int(minutes or 0))
                except (TypeError, ValueError):
                    minutes_value = 0

                task = (item.get("task") or "").strip()
                if not task:
                    continue

                title = f"[AI Plan] {task}"[:200]
                exists = Goal.objects.filter(
                    user=run.user,
                    title=title,
                    start_date=day_date,
                    due_date=day_date,
                    period_type=Goal.PERIOD_DAILY,
                ).exists()
                if exists:
                    continue

                target_type = Goal.TARGET_MINUTES if minutes_value > 0 else Goal.TARGET_TASKS
                target_value = Decimal(str(minutes_value)) if minutes_value > 0 else Decimal("1")

                Goal.objects.create(
                    user=run.user,
                    course=course,
                    title=title,
                    description=(item.get("notes") or "").strip(),
                    period_type=Goal.PERIOD_DAILY,
                    target_type=target_type,
                    target_value=target_value,
                    current_value=Decimal("0"),
                    start_date=day_date,
                    due_date=day_date,
                    priority=Goal.PRIORITY_MEDIUM,
                    status=Goal.STATUS_NOT_STARTED,
                )
                created_count += 1

        run.approved_at = run.approved_at or timezone.now()
        run.applied_at = timezone.now()
        run.save(update_fields=["approved_at", "applied_at", "updated_at"])

    return created_count


def apply_daily_plan_run(*, run: AIPlanRun) -> int:
    """
    Create daily Goal rows from a daily plan run.

    Returns: number of goals created.
    """

    if run.kind != AIPlanRun.KIND_DAILY_PLAN:
        return 0
    if run.status != AIPlanRun.STATUS_SUCCESS:
        return 0

    payload = run.effective_payload
    if not isinstance(payload, dict):
        return 0

    target_date = run.period_start
    date_value = (payload.get("date") or "").strip()
    if date_value:
        try:
            target_date = datetime.strptime(date_value, "%Y-%m-%d").date()
        except ValueError:
            pass
    if target_date is None:
        return 0

    items = payload.get("items")
    if not isinstance(items, list):
        return 0

    created_count = 0
    with transaction.atomic():
        for item in items:
            if not isinstance(item, dict):
                continue

            course_id = item.get("course_id")
            course = None
            if isinstance(course_id, int) and course_id > 0:
                course = Course.objects.filter(pk=course_id).first()

            minutes = item.get("minutes")
            try:
                minutes_value = max(0, int(minutes or 0))
            except (TypeError, ValueError):
                minutes_value = 0

            task = (item.get("task") or "").strip()
            if not task:
                continue

            title = f"[AI Plan] {task}"[:200]
            exists = Goal.objects.filter(
                user=run.user,
                title=title,
                start_date=target_date,
                due_date=target_date,
                period_type=Goal.PERIOD_DAILY,
            ).exists()
            if exists:
                continue

            target_type = Goal.TARGET_MINUTES if minutes_value > 0 else Goal.TARGET_TASKS
            target_value = Decimal(str(minutes_value)) if minutes_value > 0 else Decimal("1")

            Goal.objects.create(
                user=run.user,
                course=course,
                title=title,
                description=(item.get("notes") or "").strip(),
                period_type=Goal.PERIOD_DAILY,
                target_type=target_type,
                target_value=target_value,
                current_value=Decimal("0"),
                start_date=target_date,
                due_date=target_date,
                priority=Goal.PRIORITY_MEDIUM,
                status=Goal.STATUS_NOT_STARTED,
            )
            created_count += 1

        run.approved_at = run.approved_at or timezone.now()
        run.applied_at = timezone.now()
        run.save(update_fields=["approved_at", "applied_at", "updated_at"])

    return created_count

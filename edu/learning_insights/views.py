from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from .forms import GoalForm, NotificationPreferenceForm
from .models import Goal, InsightNotification, NotificationPreference
from .services.analytics import (
    build_monthly_summary,
    build_overview_context,
    build_weekly_summary,
)
from .services.common import get_period_start
from .services.goals import sync_goal_progress_for_user
from .services.notifications import (
    dismiss_notification,
    ensure_due_notifications,
    get_notification_payload,
    mark_notification_read,
    mark_notifications_read,
)
from .services.telegram import (
    generate_connect_token,
    get_active_connect_token,
    get_bot_username,
    get_subscription_for_user,
    queue_notification,
)


def _with_query_param(url: str, **params: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        query[key] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class InsightsBaseMixin(LoginRequiredMixin):
    """
    Shared setup for insights pages.

    Release 1 keeps the pages server-rendered and lightweight.
    On each request we refresh goal progress and generate any due in-app
    reminders so the dashboard reflects recent study activity.
    """

    def dispatch(self, request, *args, **kwargs):
        sync_goal_progress_for_user(request.user)
        ensure_due_notifications(request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_notification_preference(self) -> NotificationPreference:
        preference, _ = NotificationPreference.objects.get_or_create(
            user=self.request.user,
            defaults={
                "timezone": timezone.get_current_timezone_name(),
                "week_start_day": NotificationPreference.WEEKDAY_MONDAY,
            },
        )
        return preference


class InsightsOverviewView(InsightsBaseMixin, TemplateView):
    template_name = "learning_insights/overview.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        preference = self.get_notification_preference()
        context.update(build_overview_context(self.request.user, preference=preference))
        context["preference"] = preference
        context["page_title"] = "Learning Insights"
        return context


class WeeklySummaryView(InsightsBaseMixin, TemplateView):
    template_name = "learning_insights/weekly_summary.html"

    def _parse_week_start(self) -> date | None:
        raw = (self.request.GET.get("week") or "").strip()
        if not raw:
            return None
        parsed = parse_date(raw)
        if parsed is None:
            return None
        return parsed

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        preference = self.get_notification_preference()
        week_start = self._parse_week_start()
        context.update(
            build_weekly_summary(
                self.request.user,
                preference=preference,
                week_start=week_start,
            )
        )
        context["preference"] = preference
        context["selected_week"] = week_start
        context["page_title"] = "Weekly Summary"
        return context


class MonthlySummaryView(InsightsBaseMixin, TemplateView):
    template_name = "learning_insights/monthly_summary.html"

    def _parse_month(self) -> date | None:
        raw = (self.request.GET.get("month") or "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m").date().replace(day=1)
        except ValueError:
            return None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        preference = self.get_notification_preference()
        month_anchor = self._parse_month()
        context.update(
            build_monthly_summary(
                self.request.user,
                preference=preference,
                month_anchor=month_anchor,
            )
        )
        context["preference"] = preference
        context["page_title"] = "Monthly Summary"
        return context


class GoalQuerysetMixin(InsightsBaseMixin):
    model = Goal

    def get_queryset(self) -> QuerySet[Goal]:
        queryset = (
            Goal.objects.filter(user=self.request.user)
            .select_related("course", "parent")
            .order_by("-due_date", "-created")
        )

        status_value = (self.request.GET.get("status") or "").strip()
        period_value = (self.request.GET.get("period") or "").strip()
        course_value = (self.request.GET.get("course") or "").strip()
        priority_value = (self.request.GET.get("priority") or "").strip()

        if status_value:
            queryset = queryset.filter(status=status_value)
        if period_value:
            queryset = queryset.filter(period_type=period_value)
        if course_value.isdigit():
            queryset = queryset.filter(course_id=int(course_value))
        if priority_value:
            queryset = queryset.filter(priority=priority_value)

        return queryset


class GoalListView(GoalQuerysetMixin, ListView):
    template_name = "learning_insights/goals/list.html"
    context_object_name = "goals"
    paginate_by = 20

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        queryset = self.get_queryset()

        context["page_title"] = "Goals"
        context["status_choices"] = Goal.STATUS_CHOICES
        context["period_choices"] = Goal.PERIOD_CHOICES
        context["priority_choices"] = Goal.PRIORITY_CHOICES
        context["enrolled_courses"] = self.request.user.courses_joined.order_by("title")
        context["filters"] = {
            "status": (self.request.GET.get("status") or "").strip(),
            "period": (self.request.GET.get("period") or "").strip(),
            "course": (self.request.GET.get("course") or "").strip(),
            "priority": (self.request.GET.get("priority") or "").strip(),
        }
        context["summary"] = {
            "total": queryset.count(),
            "completed": queryset.filter(status=Goal.STATUS_COMPLETED).count(),
            "in_progress": queryset.filter(status=Goal.STATUS_IN_PROGRESS).count(),
            "overdue": queryset.filter(status=Goal.STATUS_OVERDUE).count(),
            "missed": queryset.filter(status=Goal.STATUS_MISSED).count(),
            "not_started": queryset.filter(status=Goal.STATUS_NOT_STARTED).count(),
        }
        return context


class GoalFormMixin(GoalQuerysetMixin):
    form_class = GoalForm
    success_url = reverse_lazy("learning_insights:goal_list")
    force_notifications_bootstrap = True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        url = super().get_success_url()
        if getattr(self, "force_notifications_bootstrap", False):
            return _with_query_param(url, li_bootstrap="1")
        return url

    def form_valid(self, form):
        response = super().form_valid(form)
        sync_goal_progress_for_user(self.request.user)
        messages.success(self.request, "Goal saved successfully.")
        return response


class GoalCreateView(GoalFormMixin, CreateView):
    template_name = "learning_insights/goals/form.html"

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_initial(self):
        initial = super().get_initial()
        today = timezone.localdate()
        period = (self.request.GET.get("period") or "").strip()

        if period in dict(Goal.PERIOD_CHOICES):
            initial["period_type"] = period

        selected_period = initial.get("period_type") or Goal.PERIOD_WEEKLY
        initial["start_date"] = today

        if selected_period == Goal.PERIOD_DAILY:
            initial["due_date"] = today
        elif selected_period == Goal.PERIOD_WEEKLY:
            week_start = get_period_start(today, "weekly")
            initial["start_date"] = week_start
            initial["due_date"] = week_start + timedelta(days=6)
        elif selected_period == Goal.PERIOD_MONTHLY:
            initial["start_date"] = today.replace(day=1)
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month = today.replace(month=today.month + 1, day=1)
            initial["due_date"] = next_month - timedelta(days=1)
        else:
            initial["due_date"] = today + timedelta(days=30)

        return initial

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Create Goal"
        context["form_mode"] = "create"
        return context


class GoalUpdateView(GoalFormMixin, UpdateView):
    template_name = "learning_insights/goals/form.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Edit Goal"
        context["form_mode"] = "edit"
        return context


class GoalDeleteView(GoalQuerysetMixin, DeleteView):
    template_name = "learning_insights/goals/confirm_delete.html"
    success_url = reverse_lazy("learning_insights:goal_list")

    def get_success_url(self):
        url = super().get_success_url()
        return _with_query_param(url, li_bootstrap="1")

    def form_valid(self, form):
        messages.success(self.request, "Goal deleted.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Delete Goal"
        return context


class GoalQuickActionView(GoalQuerysetMixin, View):
    """
    Minimal POST endpoint for common goal actions.

    Supported actions:
    - complete
    - carry_forward
    """

    def post(self, request, *args, **kwargs):
        goal = get_object_or_404(self.get_queryset(), pk=kwargs.get("pk"))
        action = (request.POST.get("action") or "").strip()
        today = timezone.localdate()

        if action == "complete":
            goal.current_value = goal.target_value
            goal.status = Goal.STATUS_COMPLETED
            goal.completed_at = timezone.now()
            goal.save(update_fields=["current_value", "status", "completed_at"])
            messages.success(request, "Goal marked as completed.")
        elif action == "carry_forward":
            if goal.period_type == Goal.PERIOD_DAILY:
                new_start = today + timedelta(days=1)
                new_due = new_start
            elif goal.period_type == Goal.PERIOD_WEEKLY:
                new_start = get_period_start(today + timedelta(days=7), "weekly")
                new_due = new_start + timedelta(days=6)
            elif goal.period_type == Goal.PERIOD_MONTHLY:
                current_month_start = today.replace(day=1)
                if current_month_start.month == 12:
                    new_start = current_month_start.replace(
                        year=current_month_start.year + 1,
                        month=1,
                    )
                else:
                    new_start = current_month_start.replace(
                        month=current_month_start.month + 1
                    )
                if new_start.month == 12:
                    next_month = new_start.replace(
                        year=new_start.year + 1, month=1, day=1
                    )
                elif new_start.month == 1 and new_start.year > today.year:
                    next_month = new_start.replace(month=2, day=1)
                else:
                    if new_start.month == 12:
                        next_month = new_start.replace(
                            year=new_start.year + 1, month=1, day=1
                        )
                    else:
                        next_month = new_start.replace(month=new_start.month + 1, day=1)
                new_due = next_month - timedelta(days=1)
            else:
                new_start = today
                new_due = max(goal.due_date, today + timedelta(days=7))

            goal.start_date = new_start
            goal.due_date = new_due
            goal.status = Goal.STATUS_NOT_STARTED
            if goal.target_type != Goal.TARGET_TASKS:
                goal.current_value = 0
            goal.completed_at = None
            goal.save(
                update_fields=[
                    "start_date",
                    "due_date",
                    "status",
                    "current_value",
                    "completed_at",
                ]
            )
            messages.success(request, "Goal carried forward.")
        else:
            messages.error(request, "Unknown goal action.")

        sync_goal_progress_for_user(request.user)
        return HttpResponseRedirect(
            _with_query_param(reverse("learning_insights:goal_list"), li_bootstrap="1")
        )


class NotificationPreferenceView(InsightsBaseMixin, UpdateView):
    model = NotificationPreference
    form_class = NotificationPreferenceForm
    template_name = "learning_insights/preferences/form.html"
    success_url = reverse_lazy("learning_insights:preferences")

    def get_object(self, queryset=None):
        return self.get_notification_preference()

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Notification preferences updated.")
        return response

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Preferences"
        return context


class NotificationCenterView(InsightsBaseMixin, ListView):
    model = InsightNotification
    template_name = "learning_insights/notifications/center.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[InsightNotification]:
        queryset = InsightNotification.objects.filter(user=self.request.user).order_by(
            "-scheduled_for", "-created"
        )

        category = (self.request.GET.get("category") or "").strip()
        unread_only = (self.request.GET.get("unread") or "").strip()

        if category:
            queryset = queryset.filter(category=category)
        if unread_only == "1":
            queryset = queryset.filter(read_at__isnull=True)

        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        queryset = self.get_queryset()

        context["page_title"] = "Notifications"
        context["selected_category"] = (self.request.GET.get("category") or "").strip()
        context["unread_only"] = (self.request.GET.get("unread") or "").strip()
        context["category_choices"] = InsightNotification.CATEGORY_CHOICES
        context["unread_count"] = InsightNotification.objects.filter(
            user=self.request.user,
            read_at__isnull=True,
            dismissed_at__isnull=True,
        ).count()
        context["category_count"] = (
            queryset.values_list("category", flat=True).distinct().count()
        )
        return context


class NotificationBootstrapView(LoginRequiredMixin, View):
    """
    Return lightweight notification payload for page-load toast display.
    """

    def get(self, request, *args, **kwargs):
        ensure_due_notifications(request.user)
        items = get_notification_payload(request.user, limit=4, mark_read=True)
        return JsonResponse({"items": items})


class NotificationMarkReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        notification = get_object_or_404(
            InsightNotification,
            user=request.user,
            pk=kwargs.get("pk"),
        )
        mark_notification_read(notification)

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "notification_id": notification.id})

        messages.success(request, "Notification marked as read.")
        return HttpResponseRedirect(
            request.META.get("HTTP_REFERER")
            or reverse("learning_insights:notification_center")
        )


class NotificationDismissView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        notification = get_object_or_404(
            InsightNotification,
            user=request.user,
            pk=kwargs.get("pk"),
        )
        dismiss_notification(notification)

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "notification_id": notification.id})

        messages.success(request, "Notification dismissed.")
        return HttpResponseRedirect(
            request.META.get("HTTP_REFERER")
            or reverse("learning_insights:notification_center")
        )


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        notifications = InsightNotification.objects.filter(
            user=request.user,
            read_at__isnull=True,
            dismissed_at__isnull=True,
        )
        updated = mark_notifications_read(notifications)
        if updated:
            messages.success(request, "All notifications marked as read.")
        else:
            messages.info(request, "No unread notifications to update.")
        return HttpResponseRedirect(
            request.META.get("HTTP_REFERER")
            or reverse("learning_insights:notification_center")
        )


class TelegramConnectView(InsightsBaseMixin, TemplateView):
    template_name = "learning_insights/telegram/connect.html"

    def _ensure_connect_token(self):
        token = get_active_connect_token(user=self.request.user)
        if token is None:
            token = generate_connect_token(user=self.request.user)
        return token

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Telegram"
        context["bot_username"] = get_bot_username()
        context["connect_token"] = self._ensure_connect_token()
        context["subscription"] = get_subscription_for_user(user=self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "").strip()

        if action == "regen_token":
            generate_connect_token(user=request.user)
            messages.success(request, "A new Telegram connect token was generated.")
        elif action == "queue_test":
            subscription = get_subscription_for_user(user=request.user)
            if subscription is None:
                messages.error(
                    request, "Connect Telegram first to receive a test message."
                )
            else:
                queue_notification(
                    user=request.user,
                    message="Test notification. Learning Insights is connected.",
                )
                messages.success(
                    request,
                    "Test notification queued. Run send_telegram_notifications to deliver it.",
                )
        elif action == "disconnect":
            subscription = get_subscription_for_user(user=request.user)
            if subscription is None:
                messages.info(request, "Telegram is not connected.")
            else:
                subscription.delete()
                messages.success(request, "Telegram disconnected.")
        else:
            messages.error(request, "Unknown action.")

        return HttpResponseRedirect(reverse("learning_insights:telegram_connect"))

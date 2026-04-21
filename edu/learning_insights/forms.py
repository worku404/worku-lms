from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from courses.models import Course
from django import forms

from .models import Goal, NotificationPreference


class AIPlanRunEditForm(forms.Form):
    edited_payload = forms.JSONField(
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 18,
                "spellcheck": "false",
            }
        ),
        help_text="Paste valid JSON. This edited payload is used when you click Apply.",
    )


class DateInput(forms.DateInput):
    input_type = "date"


class TimeInput(forms.TimeInput):
    input_type = "time"


class GoalForm(forms.ModelForm):
    class Meta:
        model = Goal
        fields = [
            "parent",
            "course",
            "title",
            "description",
            "period_type",
            "target_type",
            "target_value",
            "current_value",
            "start_date",
            "due_date",
            "priority",
            "status",
        ]
        widgets = {
            "parent": forms.Select(attrs={"class": "form-select"}),
            "course": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. Finish two Django modules this week",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Optional notes about this goal.",
                }
            ),
            "period_type": forms.Select(attrs={"class": "form-select"}),
            "target_type": forms.Select(attrs={"class": "form-select"}),
            "target_value": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0",
                    "step": "0.01",
                }
            ),
            "current_value": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0",
                    "step": "0.01",
                }
            ),
            "start_date": DateInput(attrs={"class": "form-control"}),
            "due_date": DateInput(attrs={"class": "form-control"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }
        help_texts = {
            "parent": "Optional parent goal for a larger long-term plan.",
            "course": "Optional course linked to this goal.",
            "target_value": "Examples: minutes, completed tasks, or completion percent.",
            "current_value": "Leave as 0 to let tracking and goal syncing update progress later.",
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        self.fields["parent"].required = False
        self.fields["course"].required = False
        self.fields["description"].required = False
        self.fields["current_value"].required = False

        self.fields["current_value"].initial = (
            self.initial.get("current_value")
            if "current_value" in self.initial
            else self.instance.current_value
            if getattr(self.instance, "pk", None)
            else 0
        )

        course_field = self.fields["course"]
        parent_field = self.fields["parent"]

        if isinstance(course_field, forms.ModelChoiceField):
            course_field.queryset = Course.objects.none()
        if isinstance(parent_field, forms.ModelChoiceField):
            parent_field.queryset = Goal.objects.none()

        if user is not None and getattr(user, "is_authenticated", False):
            current_course_id = getattr(self.instance, "course_id", None)
            course_qs = user.courses_joined.all().order_by("title")
            if self.instance.pk and current_course_id:
                course_qs = (
                    user.courses_joined.filter(pk=current_course_id) | course_qs
                ).distinct()

            parent_qs = Goal.objects.filter(user=user).order_by("due_date", "title")
            if self.instance.pk:
                parent_qs = parent_qs.exclude(pk=self.instance.pk)

            if isinstance(course_field, forms.ModelChoiceField):
                course_field.queryset = course_qs
            if isinstance(parent_field, forms.ModelChoiceField):
                parent_field.queryset = parent_qs

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        if parent and self.instance.pk and parent.pk == self.instance.pk:
            raise forms.ValidationError("A goal cannot be its own parent.")
        return parent

    def clean_target_value(self):
        value = self.cleaned_data.get("target_value")
        if value is not None and value < 0:
            raise forms.ValidationError("Target value cannot be negative.")
        return value

    def clean_current_value(self):
        value = self.cleaned_data.get("current_value")
        if value in (None, ""):
            return 0
        if value < 0:
            raise forms.ValidationError("Current value cannot be negative.")
        return value

    def clean(self):
        cleaned_data = super().clean() or {}
        start_date = cleaned_data.get("start_date")
        due_date = cleaned_data.get("due_date")
        target_value = cleaned_data.get("target_value")
        current_value = cleaned_data.get("current_value")
        status = cleaned_data.get("status")

        if start_date and due_date and due_date < start_date:
            self.add_error(
                "due_date", "Due date cannot be earlier than the start date."
            )

        if (
            status == Goal.STATUS_COMPLETED
            and target_value is not None
            and current_value is not None
            and current_value < target_value
        ):
            cleaned_data["current_value"] = target_value

        return cleaned_data


class NotificationPreferenceForm(forms.ModelForm):
    class Meta:
        model = NotificationPreference
        fields = [
            "timezone",
            "week_start_day",
            "daily_enabled",
            "weekly_enabled",
            "daily_achievement_enabled",
            "weekly_achievement_enabled",
            "in_app_enabled",
            "telegram_enabled",
            "telegram_daily_summary_enabled",
            "telegram_weekly_review_enabled",
            "telegram_critical_alerts_enabled",
            "daily_time",
            "weekly_time",
        ]
        widgets = {
            "timezone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. UTC or Africa/Addis_Ababa",
                }
            ),
            "week_start_day": forms.Select(attrs={"class": "form-select"}),
            "daily_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "weekly_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "daily_achievement_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "weekly_achievement_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "in_app_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "telegram_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "telegram_daily_summary_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "telegram_weekly_review_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "telegram_critical_alerts_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "daily_time": TimeInput(attrs={"class": "form-control"}),
            "weekly_time": TimeInput(attrs={"class": "form-control"}),
        }
        help_texts = {
            "timezone": "Used to calculate your local day, week, reminders, and summaries.",
            "week_start_day": "Controls how weekly goals and summaries are grouped.",
            "daily_time": "Preferred local time for daily in-app reminders.",
            "weekly_time": "Preferred local time for weekly in-app reminders.",
            "telegram_enabled": "Enable Telegram delivery (requires connecting Telegram first).",
            "telegram_daily_summary_enabled": "Optional daily AI summary to Telegram.",
            "telegram_weekly_review_enabled": "Weekly AI review notification to Telegram.",
            "telegram_critical_alerts_enabled": "Critical Telegram alerts when risk patterns are detected.",
        }

    def clean_timezone(self):
        value = (self.cleaned_data.get("timezone") or "").strip() or "UTC"
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise forms.ValidationError(
                "Enter a valid IANA timezone, such as UTC or Africa/Addis_Ababa."
            )
        return value

    def clean(self):
        cleaned_data = super().clean() or {}

        daily_enabled = cleaned_data.get("daily_enabled")
        weekly_enabled = cleaned_data.get("weekly_enabled")
        in_app_enabled = cleaned_data.get("in_app_enabled")
        telegram_enabled = cleaned_data.get("telegram_enabled")
        telegram_daily = cleaned_data.get("telegram_daily_summary_enabled")
        telegram_weekly = cleaned_data.get("telegram_weekly_review_enabled")
        telegram_critical = cleaned_data.get("telegram_critical_alerts_enabled")
        daily_time = cleaned_data.get("daily_time")
        weekly_time = cleaned_data.get("weekly_time")

        if daily_enabled and not daily_time:
            self.add_error(
                "daily_time",
                "Daily reminder time is required when daily reminders are enabled.",
            )

        if weekly_enabled and not weekly_time:
            self.add_error(
                "weekly_time",
                "Weekly reminder time is required when weekly reminders are enabled.",
            )

        if not in_app_enabled and (
            daily_enabled
            or weekly_enabled
            or cleaned_data.get("daily_achievement_enabled")
            or cleaned_data.get("weekly_achievement_enabled")
        ):
            raise forms.ValidationError(
                "Enable in-app notifications to receive Release 1 insight notifications."
            )

        if (telegram_daily or telegram_weekly or telegram_critical) and not telegram_enabled:
            raise forms.ValidationError("Enable Telegram to use Telegram notification settings.")

        return cleaned_data

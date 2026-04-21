from __future__ import annotations

from django.core.management.base import BaseCommand

from learning_insights.models import TelegramSubscription
from learning_insights.services.ai_notifications import (
    maybe_send_critical_alerts,
    maybe_send_daily_summary,
    maybe_send_weekly_review,
)
from learning_insights.services.common import get_or_create_notification_preference


class Command(BaseCommand):
    help = "Generate and send Telegram coaching notifications based on scheduled + event triggers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Only generate notifications for a single user id.",
        )

    def handle(self, *args, **options):
        qs = TelegramSubscription.objects.select_related("user").order_by("id")
        if options.get("user_id"):
            qs = qs.filter(user_id=int(options["user_id"]))

        daily_count = 0
        weekly_count = 0
        critical_count = 0

        for subscription in qs:
            user = subscription.user
            preference = get_or_create_notification_preference(user)

            if maybe_send_critical_alerts(user=user, preference=preference):
                critical_count += 1
                continue
            if maybe_send_daily_summary(user=user, preference=preference):
                daily_count += 1
            if maybe_send_weekly_review(user=user, preference=preference):
                weekly_count += 1

        self.stdout.write(
            f"Telegram notifications: daily={daily_count}, weekly={weekly_count}, critical={critical_count}."
        )

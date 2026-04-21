from __future__ import annotations

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand

from learning_insights.models import TelegramSubscription
from learning_insights.services.ai_notifications import (
    maybe_send_critical_alerts,
    maybe_send_daily_summary,
    maybe_send_weekly_review,
)
from learning_insights.services.common import get_or_create_notification_preference
from learning_insights.services.telegram import (
    TelegramUpdateProcessor,
    fetch_updates,
    get_bot_token,
)


class Command(BaseCommand):
    help = (
        "Run a single background worker: poll Telegram updates (getUpdates) and "
        "send scheduled + event-based Learning Insights notifications."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single loop iteration and exit (useful for schedulers).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=25,
            help="Long polling timeout in seconds (Telegram getUpdates timeout).",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=1.0,
            help="Delay between loops in seconds.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Maximum updates to fetch per request.",
        )
        parser.add_argument(
            "--offset",
            type=int,
            default=None,
            help="Override the stored offset for the next poll.",
        )
        parser.add_argument(
            "--reset-offset",
            action="store_true",
            help="Forget the stored offset and start from the latest available updates.",
        )
        parser.add_argument(
            "--notify-every",
            type=float,
            default=60.0,
            help="How often to evaluate notification rules (seconds).",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Only process a single user id (useful for local testing).",
        )

    def handle(self, *args, **options):
        token = get_bot_token()
        if not token:
            self.stderr.write(
                "TELEGRAM_BOT_TOKEN is not configured. Set it in your environment or .env."
            )
            return

        cache_key = "li_telegram_update_offset"
        if options["reset_offset"]:
            cache.delete(cache_key)

        offset = options["offset"]
        if offset is None:
            offset = cache.get(cache_key)

        timeout = int(options["timeout"] or 25)
        limit = int(options["limit"] or 100)
        sleep_seconds = float(options["sleep"] or 0)
        notify_every = float(options["notify_every"] or 60.0)
        run_once = bool(options["once"])
        target_user_id = options.get("user_id")

        processor = TelegramUpdateProcessor(token=token)
        last_notify_at = 0.0

        self.stdout.write(
            "Learning Insights worker started "
            f"(timeout={timeout}s, limit={limit}, offset={offset}, notify_every={notify_every}s)."
        )

        while True:
            try:
                updates = fetch_updates(
                    token=token,
                    offset=offset,
                    timeout=timeout,
                    limit=limit,
                )
                if updates:
                    processor.process_updates(updates)
                    max_update_id = max(
                        (
                            update.get("update_id")
                            for update in updates
                            if isinstance(update.get("update_id"), int)
                        ),
                        default=None,
                    )
                    if isinstance(max_update_id, int):
                        offset = max_update_id + 1
                        cache.set(cache_key, offset, timeout=None)
            except KeyboardInterrupt:
                self.stdout.write("Stopped.")
                return
            except Exception as exc:
                self.stderr.write(f"Worker polling error: {exc}")

            now = time.time()
            if now - last_notify_at >= notify_every:
                last_notify_at = now
                self._dispatch_notifications(user_id=target_user_id)

            if run_once:
                return

            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    def _dispatch_notifications(self, *, user_id: int | None = None) -> None:
        qs = TelegramSubscription.objects.select_related("user").order_by("id")
        if user_id:
            qs = qs.filter(user_id=int(user_id))

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

        if daily_count or weekly_count or critical_count:
            self.stdout.write(
                f"Notifications sent: daily={daily_count}, weekly={weekly_count}, critical={critical_count}."
            )

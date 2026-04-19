from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from learning_insights.models import NotificationQueue, TelegramSubscription
from learning_insights.services.telegram import get_bot_token, send_message


class Command(BaseCommand):
    help = "Send pending Telegram messages stored in learning_insights.NotificationQueue."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Maximum pending messages to send in one run.",
        )
        parser.add_argument(
            "--max-attempts",
            type=int,
            default=5,
            help="Mark messages as failed after this many attempts.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without sending.",
        )

    def handle(self, *args, **options):
        token = get_bot_token()
        if not token:
            self.stderr.write(
                "TELEGRAM_BOT_TOKEN is not configured. Set it in your environment or .env."
            )
            return

        batch_size = int(options["batch_size"] or 50)
        max_attempts = int(options["max_attempts"] or 5)
        dry_run = bool(options["dry_run"])

        pending = (
            NotificationQueue.objects.filter(status=NotificationQueue.STATUS_PENDING)
            .select_related("user")
            .order_by("created_at", "id")[:batch_size]
        )

        sent_count = 0
        failed_count = 0
        skipped_count = 0

        for item in pending:
            subscription = TelegramSubscription.objects.filter(user=item.user).first()
            if subscription is None:
                skipped_count += 1
                if not item.last_error:
                    item.last_error = "User has no Telegram subscription yet."
                    item.last_attempted_at = timezone.now()
                    item.save(update_fields=["last_error", "last_attempted_at"])
                continue

            if item.attempts >= max_attempts:
                item.status = NotificationQueue.STATUS_FAILED
                item.last_error = item.last_error or "Max attempts exceeded."
                item.last_attempted_at = timezone.now()
                item.save(update_fields=["status", "last_error", "last_attempted_at"])
                failed_count += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] Would send to {subscription.chat_id}: {item.message}")
                continue

            item.attempts += 1
            item.last_attempted_at = timezone.now()
            item.save(update_fields=["attempts", "last_attempted_at"])

            try:
                send_message(token=token, chat_id=int(subscription.chat_id), text=item.message)
            except Exception as exc:
                item.last_error = str(exc)[:2000]
                if item.attempts >= max_attempts:
                    item.status = NotificationQueue.STATUS_FAILED
                item.save(update_fields=["status", "last_error"])
                failed_count += 1
                continue

            item.status = NotificationQueue.STATUS_SENT
            item.sent_at = timezone.now()
            item.last_error = ""
            item.save(update_fields=["status", "sent_at", "last_error"])
            sent_count += 1

        self.stdout.write(
            f"Telegram notifications: sent={sent_count}, failed={failed_count}, skipped={skipped_count}."
        )


"""Retry pending and failed webhook deliveries.

    docker compose exec web python manage.py deliver_webhooks

Run it by hand after fixing a receiver, or from cron for automatic retries.
"""

from django.core.management.base import BaseCommand

from integrations.services import retry_failed


class Command(BaseCommand):
    help = "Retry pending and failed webhook deliveries."

    def handle(self, *args, **options):
        out = retry_failed()
        self.stdout.write(f"tried {out['tried']}, delivered {out['ok']}, still failing {out['failed']}")

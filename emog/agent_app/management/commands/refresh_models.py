from django.core.management.base import BaseCommand
from agent_app.views import get_model_ids_cached
import json


class Command(BaseCommand):
    help = 'Refreshes the cached model id list from OmniRoute and prints a summary.'

    def handle(self, *args, **options):
        ids = []
        try:
            ids = get_model_ids_cached()
        except Exception as e:
            self.stderr.write('Failed to refresh models: ' + str(e))
            return

        self.stdout.write(f'Refreshed {len(ids)} model ids. Top 10:')
        for m in ids[:10]:
            self.stdout.write(' - ' + str(m))
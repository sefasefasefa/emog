import time
import json
import urllib.request
import urllib.error
from datetime import datetime
import os

from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Poll OmniRoute health periodically and append JSON lines to a log file."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=60, help="Seconds between polls")
        parser.add_argument("--logfile", type=str, default="omni_health.log", help="Log file path")
        parser.add_argument("--once", action="store_true", help="Run a single check and exit")

    def handle(self, *args, **options):
        interval = options.get("interval", 60)
        logfile = options.get("logfile")
        once = options.get("once", False)

        omni_cfg = getattr(settings, "OMNIROUTE", {})
        base = omni_cfg.get("BASE_URL", "http://localhost:3001/v1")
        timeout = omni_cfg.get("TIMEOUT", 5)

        candidates = [
            base,
            base.rstrip("/") + "/health",
            base.rstrip("/") + "/models",
            base.rstrip("/") + "/v1/health",
            base.rstrip("/") + "/v1/models",
        ]

        def probe():
            last_err = None
            for url in candidates:
                try:
                    with urllib.request.urlopen(url, timeout=timeout) as resp:
                        status = resp.getcode()
                        body = resp.read(4096).decode(errors="replace")
                        entry = {
                            "ts": datetime.utcnow().isoformat() + "Z",
                            "ok": True,
                            "url": url,
                            "status": status,
                            "body": body,
                        }
                        return entry
                except urllib.error.HTTPError as e:
                    return {"ts": datetime.utcnow().isoformat() + "Z", "ok": False, "url": url, "status": e.code, "error": str(e)}
                except Exception as e:
                    last_err = str(e)
                    continue

            return {"ts": datetime.utcnow().isoformat() + "Z", "ok": False, "error": f"All probes failed. Last error: {last_err}"}

        # Ensure logfile directory exists
        logdir = os.path.dirname(os.path.abspath(logfile))
        if logdir and not os.path.exists(logdir):
            os.makedirs(logdir, exist_ok=True)

        # Run
        while True:
            entry = probe()
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.stdout.write(json.dumps(entry, ensure_ascii=False))
            if once:
                break
            time.sleep(interval)

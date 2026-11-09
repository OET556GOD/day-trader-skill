"""Entry point: `gunicorn 'assistant.main:app'` or `python -m assistant.main`.

Run ONE worker process: calls in progress live in memory and the scheduler
must not run twice (it would phone you twice)."""
from __future__ import annotations

import logging

from .brain import Assistant
from .config import Settings
from .gmail import Gmail
from .scheduler import Scheduler
from .server import create_app
from .tasks import TaskStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build():
    settings = Settings.from_env()
    missing = [k for k, v in {"TWILIO_ACCOUNT_SID": settings.twilio_sid,
                              "TWILIO_AUTH_TOKEN": settings.twilio_token,
                              "TWILIO_PHONE_NUMBER": settings.twilio_number,
                              "OWNER_PHONE_NUMBER": settings.owner_number,
                              "PUBLIC_BASE_URL": settings.public_base_url}.items() if not v]
    if missing:
        raise SystemExit("Missing settings: " + ", ".join(missing) + " (see .env.example).\n"
                         "To just talk to it without a phone, run: python -m assistant.chat")
    tasks = TaskStore(settings.data_dir / "tasks.json")
    gmail = (Gmail(settings.gmail_client_id, settings.gmail_client_secret, settings.gmail_refresh_token)
             if settings.gmail_configured else None)
    assistant = Assistant(settings, tasks, gmail)
    app = create_app(settings, assistant)
    if settings.start_scheduler:
        Scheduler(settings, tasks, app.config["PLACE_CALL"]).start()
    return app


app = build()

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

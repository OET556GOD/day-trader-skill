"""Talk to the assistant by typing, no phone or Twilio needed.

    python -m assistant.chat            # like calling it
    python -m assistant.chat morning    # hear the morning briefing first
"""
from __future__ import annotations

import sys

from .brain import Assistant
from .config import Settings
from .gmail import Gmail
from .tasks import TaskStore


def main() -> None:
    s = Settings.from_env()
    gmail = Gmail(s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token) if s.gmail_configured else None
    bot = Assistant(s, TaskStore(s.data_dir / "tasks.json"), gmail)
    kind = sys.argv[1] if len(sys.argv) > 1 else "incoming"
    conv, turn = bot.start(kind)
    print(f"assistant: {turn.text}")
    while not turn.end_call:
        try:
            said = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if said:
            turn = bot.reply(conv, said)
            print(f"assistant: {turn.text}")


if __name__ == "__main__":
    main()

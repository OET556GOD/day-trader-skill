"""Checks your setup and says in plain words what's missing.

    python -m assistant.doctor
"""
from __future__ import annotations

import importlib
import os
import sys

OK, BAD = "OK  ", "FIX "


def main() -> int:
    problems = 0

    def report(good: bool, msg: str, fix: str = "") -> None:
        nonlocal problems
        print((OK if good else BAD) + msg + ("" if good or not fix else f"\n      -> {fix}"))
        problems += 0 if good else 1

    v = sys.version_info
    report(v >= (3, 10), f"Python {v.major}.{v.minor}",
           "Install Python 3.10 or newer from python.org, then run with python3")

    for mod, pkg in [("anthropic", "anthropic"), ("flask", "flask"), ("twilio", "twilio"),
                     ("tzdata", "tzdata"), ("googleapiclient", "google-api-python-client")]:
        try:
            importlib.import_module(mod)
            report(True, f"package {pkg}")
        except ImportError:
            report(False, f"package {pkg} missing", "python -m pip install -r requirements.txt")

    try:
        from .config import Settings
        s = Settings.from_env()
    except Exception as e:  # bad CHECKIN_TIMES etc.
        report(False, f"settings: {e}", "check your .env file against .env.example")
        return 1

    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(s.timezone)
        report(True, f"time zone {s.timezone}")
    except Exception:
        report(False, f"time zone {s.timezone} not found", "python -m pip install tzdata")

    report(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set",
           "create a key at console.anthropic.com and put ANTHROPIC_API_KEY=... in a file "
           "named .env in this folder")
    report(s.plan_path.is_file(), "plan.md found", "run this from inside the phone-assistant folder")
    print("\nFor phone calls (not needed for `python -m assistant.chat`):")
    for name, val in [("TWILIO_ACCOUNT_SID", s.twilio_sid), ("TWILIO_AUTH_TOKEN", s.twilio_token),
                      ("TWILIO_PHONE_NUMBER", s.twilio_number), ("OWNER_PHONE_NUMBER", s.owner_number),
                      ("PUBLIC_BASE_URL", s.public_base_url)]:
        print(("  set     " if val else "  not set ") + name)
    print("  Gmail: " + ("connected" if s.gmail_configured else "not set up (optional)"))

    if os.environ.get("ANTHROPIC_API_KEY") and not problems:
        try:
            import anthropic
            anthropic.Anthropic().models.retrieve(s.anthropic_model)
            report(True, f"Anthropic key works, model {s.anthropic_model} available")
        except Exception as e:
            report(False, f"Anthropic API check failed: {e}",
                   "check the key is correct and your account has credit")

    print("\n" + ("All good. Try: python -m assistant.chat morning" if not problems
                  else f"{problems} thing(s) to fix above."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

"""Settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so local runs work without extra packages."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def parse_checkins(raw: str) -> dict[str, str]:
    """'morning=07:00,evening=20:00' -> {'morning': '07:00', 'evening': '20:00'}"""
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, hhmm = part.partition("=")
        hh, _, mm = hhmm.strip().partition(":")
        if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
            raise ValueError(f"Bad CHECKIN_TIMES entry: {part!r} (want name=HH:MM)")
        out[name.strip()] = f"{int(hh):02d}:{int(mm):02d}"
    return out


@dataclass
class Settings:
    anthropic_model: str = "claude-opus-5-5"
    effort: str = "low"
    twilio_sid: str = ""
    twilio_token: str = ""
    twilio_number: str = ""
    owner_number: str = ""
    owner_name: str = "there"
    public_base_url: str = ""
    admin_token: str = ""
    timezone: str = "America/New_York"
    checkins: dict[str, str] = field(default_factory=dict)
    checkin_days: set[int] = field(default_factory=lambda: set(range(7)))
    voice: str = "Polly.Joanna-Neural"
    data_dir: Path = ROOT / "data"
    plan_path: Path = ROOT / "plan.md"
    start_scheduler: bool = True
    validate_twilio: bool = True
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""

    @property
    def gmail_configured(self) -> bool:
        return bool(self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token)

    def plan_text(self) -> str:
        try:
            return self.plan_path.read_text()
        except OSError:
            return "(no training plan file found)"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(ROOT / ".env")
        env = os.environ.get
        return cls(
            anthropic_model=env("ASSISTANT_MODEL", "claude-opus-5-5"),
            effort=env("ASSISTANT_EFFORT", "low"),
            twilio_sid=env("TWILIO_ACCOUNT_SID", ""),
            twilio_token=env("TWILIO_AUTH_TOKEN", ""),
            twilio_number=env("TWILIO_PHONE_NUMBER", ""),
            owner_number=env("OWNER_PHONE_NUMBER", ""),
            owner_name=env("OWNER_NAME", "there"),
            public_base_url=env("PUBLIC_BASE_URL", "").rstrip("/"),
            admin_token=env("ADMIN_TOKEN", ""),
            timezone=env("TIMEZONE", "America/New_York"),
            checkins=parse_checkins(
                env("CHECKIN_TIMES", "morning=07:00,late-morning=11:00,midday=12:30,evening=20:00")),
            checkin_days={int(d) for d in env("CHECKIN_DAYS", "0,1,2,3,4,5,6").split(",") if d.strip()},
            voice=env("VOICE", "Polly.Joanna-Neural"),
            data_dir=Path(env("DATA_DIR", str(ROOT / "data"))),
            start_scheduler=env("START_SCHEDULER", "true").lower() == "true",
            validate_twilio=env("VALIDATE_TWILIO", "true").lower() == "true",
            gmail_client_id=env("GMAIL_CLIENT_ID", ""),
            gmail_client_secret=env("GMAIL_CLIENT_SECRET", ""),
            gmail_refresh_token=env("GMAIL_REFRESH_TOKEN", ""),
        )

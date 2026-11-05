"""Offline tests: fake Claude, fake Twilio, fake Gmail. No network, no keys."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest
from twilio.request_validator import RequestValidator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.brain import TOOLS, Assistant, Conversation  # noqa: E402
from assistant.config import Settings, parse_checkins  # noqa: E402
from assistant.scheduler import Scheduler, due_checkins  # noqa: E402
from assistant.server import create_app  # noqa: E402
from assistant.tasks import TaskStore  # noqa: E402

TZ = ZoneInfo("America/New_York")
OWNER = "+15550001111"


def text(t):
    return NS(type="text", text=t)


def tool(name, args, id="tu_1"):
    return NS(type="tool_use", name=name, input=args, id=id)


class FakeClaude:
    """Returns scripted responses and records each request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        content, stop = self.script.pop(0)
        return NS(content=content, stop_reason=stop)


class FakeGmail:
    def __init__(self):
        self.drafts = []

    def list_messages(self, query=None, limit=10):
        return [{"id": "m1", "thread_id": "t1", "from": "Alyce <a@x.com>",
                 "subject": "Time off Nov 21", "date": "", "snippet": "Can I take the 21st?"}]

    def read_message(self, mid):
        return {"id": mid, "body": "Can I take Saturday the 21st off?"}

    def create_reply_draft(self, mid, body):
        self.drafts.append((mid, body))
        return {"draft_id": "d1", "to": "a@x.com", "subject": "Re: Time off Nov 21"}


@pytest.fixture
def settings(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("Strength Mon/Wed/Fri 6pm.")
    return Settings(twilio_token="secret", twilio_number="+15559990000", owner_number=OWNER,
                    owner_name="Teo", public_base_url="https://bot.example.com", admin_token="adm",
                    checkins=parse_checkins("morning=07:00,evening=20:00"),
                    data_dir=tmp_path, plan_path=plan, validate_twilio=False)


@pytest.fixture
def tasks(tmp_path):
    return TaskStore(tmp_path / "tasks.json")


# ---- config / tasks / scheduler -------------------------------------------
def test_parse_checkins():
    assert parse_checkins("morning=7:00, evening=20:05") == {"morning": "07:00", "evening": "20:05"}
    with pytest.raises(ValueError):
        parse_checkins("morning=25:00")


def test_tools_are_strict_and_closed():
    for t in TOOLS:
        assert t["strict"] is True
        schema = t["input_schema"]
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
    assert "send" not in " ".join(t["name"] for t in TOOLS)


def test_task_store_roundtrip(tasks):
    t = tasks.add("Call the bank", "2026-11-05T15:00")
    tasks.add("Buy protein")
    assert len(tasks.open_tasks()) == 2
    assert tasks.due_reminders(datetime(2026, 11, 5, 14, 59, tzinfo=TZ)) == []
    assert [d["id"] for d in tasks.due_reminders(datetime(2026, 11, 5, 15, 0, tzinfo=TZ))] == [t["id"]]
    tasks.mark_reminded(t["id"])
    assert tasks.due_reminders(datetime(2026, 11, 5, 16, 0, tzinfo=TZ)) == []
    assert tasks.complete("protein")["title"] == "Buy protein"
    assert [x["title"] for x in tasks.open_tasks()] == ["Call the bank"]
    with pytest.raises(ValueError):
        tasks.add("bad", "tomorrow-ish")


def test_due_checkins_window_and_once():
    sched = {"morning": "07:00"}
    fired: set[str] = set()
    assert due_checkins(datetime(2026, 11, 5, 6, 59, tzinfo=TZ), sched, set(range(7)), fired) == []
    assert due_checkins(datetime(2026, 11, 5, 7, 4, tzinfo=TZ), sched, set(range(7)), fired) == ["morning"]
    assert due_checkins(datetime(2026, 11, 5, 7, 11, tzinfo=TZ), sched, set(range(7)), fired) == []
    fired.add("2026-11-05:morning")
    assert due_checkins(datetime(2026, 11, 5, 7, 1, tzinfo=TZ), sched, set(range(7)), fired) == []
    # Thursday excluded
    assert due_checkins(datetime(2026, 11, 5, 7, 0, tzinfo=TZ), sched, {5, 6}, set()) == []


def test_scheduler_places_checkin_and_reminder_once(settings, tasks):
    calls = []
    tasks.add("Send invoice", "2026-11-05T07:00")
    s = Scheduler(settings, tasks, lambda kind, extra="": calls.append((kind, extra)))
    now = datetime(2026, 11, 5, 7, 0, 30, tzinfo=TZ)
    assert s.tick(now)[0] == "morning"
    assert s.tick(now) == []
    assert [k for k, _ in calls] == ["morning", "reminder"]
    assert "Send invoice" in calls[1][1]


# ---- brain -----------------------------------------------------------------
def test_tool_loop_drafts_but_never_sends(settings, tasks):
    gmail = FakeGmail()
    claude = FakeClaude([
        ([tool("check_email", {"query": ""})], "tool_use"),
        ([text("Alyce wants the 21st off. Want me to draft a yes?")], "end_turn"),
        ([tool("draft_reply", {"message_id": "m1", "body": "Yes, approved. - Teo"}, "tu_2")], "tool_use"),
        ([text("Draft saved in Gmail for you to send.")], "end_turn"),
    ])
    bot = Assistant(settings, tasks, gmail, client=claude)
    conv, first = bot.start("morning")
    assert "Alyce" in first.text and not first.end_call
    turn = bot.reply(conv, "yes draft it")
    assert "Draft saved" in turn.text
    assert gmail.drafts == [("m1", "Yes, approved. - Teo")]
    req = claude.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in req and "tool_choice" not in req
    assert "Strength Mon/Wed/Fri" in req["system"]
    # tool result was fed back as a user turn matching the tool_use id
    assert claude.requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "tu_1"


def test_add_task_and_end_call(settings, tasks):
    claude = FakeClaude([
        ([tool("add_task", {"title": "Gym bag", "remind_at": "2026-11-05T17:30"})], "tool_use"),
        ([text("Got it, I'll call you at five thirty. Bye!"), tool("end_call", {}, "tu_9")], "tool_use"),
    ])
    bot = Assistant(settings, tasks, None, client=claude)
    turn = bot.reply(Conversation(kind="incoming"), "remind me to pack my gym bag at 5:30, bye")
    assert turn.end_call and "five thirty" in turn.text
    assert tasks.open_tasks()[0]["remind_at"] == "2026-11-05T17:30"


def test_gmail_missing_is_reported_not_crashed(settings, tasks):
    claude = FakeClaude([([tool("check_email", {"query": ""})], "tool_use"),
                         ([text("Gmail isn't connected yet.")], "end_turn")])
    bot = Assistant(settings, tasks, None, client=claude)
    _, first = bot.start("morning")
    result = claude.requests[1]["messages"][-1]["content"][0]
    assert "not connected" in result["content"] and result["is_error"] is False
    assert first.text == "Gmail isn't connected yet."


def test_refusal_is_spoken_politely(settings, tasks):
    bot = Assistant(settings, tasks, None, client=FakeClaude([([], "refusal")]))
    _, first = bot.start("incoming")
    assert "can't help" in first.text


# ---- server ----------------------------------------------------------------
class FakeTwilio:
    def __init__(self):
        self.placed = []
        self.calls = NS(create=self.create)

    def create(self, **kw):
        self.placed.append(kw)
        return NS(sid="CA123")


def make_app(settings, tasks, script):
    claude = FakeClaude(script)
    bot = Assistant(settings, tasks, FakeGmail(), client=claude)
    tw = FakeTwilio()
    app = create_app(settings, bot, twilio_client=tw)
    return app, tw, claude


def test_call_now_requires_token_and_plays_prepared_briefing(settings, tasks):
    app, tw, _ = make_app(settings, tasks, [([text("Good morning Teo. Three things today.")], "end_turn")])
    c = app.test_client()
    assert c.post("/call-now").status_code == 401
    assert c.post("/call-now", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = c.post("/call-now?kind=morning", headers={"Authorization": "Bearer adm"})
    assert r.json == {"call_sid": "CA123"}
    assert tw.placed[0]["to"] == OWNER and tw.placed[0]["url"] == "https://bot.example.com/voice/answer"
    xml = c.post("/voice/answer", data={"CallSid": "CA123"}).get_data(as_text=True)
    assert "Good morning Teo" in xml and "<Gather" in xml and "/voice/turn" in xml


def test_incoming_rejects_strangers_and_greets_owner(settings, tasks):
    app, _, _ = make_app(settings, tasks, [])
    c = app.test_client()
    assert "private" in c.post("/voice/incoming", data={"From": "+19998887777", "CallSid": "X"}).get_data(as_text=True)
    xml = c.post("/voice/incoming", data={"From": OWNER, "CallSid": "CA9"}).get_data(as_text=True)
    assert "Hey Teo" in xml and "<Gather" in xml


def test_turn_speech_reply_and_hangup(settings, tasks):
    app, _, claude = make_app(settings, tasks, [
        ([text("You have no open tasks. Anything else?")], "end_turn"),
        ([text("Bye Teo!"), tool("end_call", {})], "tool_use"),
    ])
    c = app.test_client()
    c.post("/voice/incoming", data={"From": OWNER, "CallSid": "CA9"})
    xml = c.post("/voice/turn", data={"CallSid": "CA9", "SpeechResult": "what's on my list"}).get_data(as_text=True)
    assert "no open tasks" in xml
    assert "[Incoming call from Teo." in claude.requests[0]["messages"][0]["content"]
    xml = c.post("/voice/turn", data={"CallSid": "CA9", "SpeechResult": "that's all, bye"}).get_data(as_text=True)
    assert "Bye Teo" in xml and "<Hangup" in xml


def test_silence_reprompts_then_hangs_up(settings, tasks):
    app, _, _ = make_app(settings, tasks, [])
    c = app.test_client()
    for _ in range(2):
        assert "still there" in c.post("/voice/turn", data={"CallSid": "S"}).get_data(as_text=True)
    assert "<Hangup" in c.post("/voice/turn", data={"CallSid": "S"}).get_data(as_text=True)


def test_twilio_signature_is_enforced(settings, tasks):
    settings.validate_twilio = True
    app, _, _ = make_app(settings, tasks, [])
    c = app.test_client()
    form = {"From": OWNER, "CallSid": "CA1"}
    assert c.post("/voice/incoming", data=form).status_code == 403
    sig = RequestValidator("secret").compute_signature("https://bot.example.com/voice/incoming", form)
    r = c.post("/voice/incoming", data=form, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "Hey Teo" in r.get_data(as_text=True)


def test_slow_reply_holds_the_line_then_answers(settings, tasks, monkeypatch):
    import threading
    import assistant.server as server
    monkeypatch.setattr(server, "INLINE_WAIT", 0.05)
    gate = threading.Event()
    app, _, claude = make_app(settings, tasks, [([text("Here's your answer.")], "end_turn")])
    real = claude.create
    claude.beta.messages.create = lambda **kw: (gate.wait(5), real(**kw))[1]
    c = app.test_client()
    xml = c.post("/voice/turn", data={"CallSid": "H", "SpeechResult": "hard question"}).get_data(as_text=True)
    assert "One moment" in xml and "/voice/hold?n=1" in xml
    gate.set()
    import time
    time.sleep(0.2)
    xml = c.post("/voice/hold?n=1", data={"CallSid": "H"}).get_data(as_text=True)
    assert "Here's your answer." in xml and "<Gather" in xml

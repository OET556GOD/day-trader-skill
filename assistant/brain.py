"""The assistant's brain: one Claude conversation per phone call, with tools
for tasks, the training plan and Gmail (read + draft, never send)."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from .config import Settings
from .tasks import TaskStore

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_TEMPLATE = """You are {name}'s personal daily assistant, speaking with them on a PHONE CALL.

How to talk:
- Everything you write is read aloud by text-to-speech. Plain spoken sentences only: no markdown, \
bullets, emoji, URLs or symbols. Say times like "nine thirty".
- Be brief: two to four short sentences per turn unless they ask for more. Offer detail, don't dump it.
- End most turns with a short question so they know it's their turn.
- When they say goodbye, or clearly want to hang up, say a short goodbye and call end_call.

What you help with: their daily priorities, the training plan below, their task list and reminders, \
and their email.

Email rules:
- check_email and read_email to see their inbox. Skip newsletters, marketing and automated alerts \
unless they ask. Mention security alerts (sign-in or verification codes they didn't expect).
- You can only write DRAFTS with draft_reply. You cannot send email. Tell them the draft is in \
Gmail for them to review and send. Before drafting, tell them in one sentence what you'll say.
- Never read out verification codes, passwords or full account numbers.

Tasks: add_task when they ask you to remember or remind them of something. If they give a time, \
pass it as remind_at in local time and you will phone them then. complete_task when they say \
something is done.

Don't invent emails, tasks or events. If a tool fails, say so plainly.

Their training plan:
{plan}
"""

TOOLS = [
    {"name": "list_tasks", "description": "List the user's open tasks and reminders.",
     "input_schema": {"type": "object", "properties": {}, "required": [],
                      "additionalProperties": False}},
    {"name": "add_task",
     "description": "Add a task. remind_at is local time 'YYYY-MM-DDTHH:MM' for a reminder call, "
                    "or an empty string for no reminder.",
     "input_schema": {"type": "object",
                      "properties": {"title": {"type": "string"}, "remind_at": {"type": "string"}},
                      "required": ["title", "remind_at"], "additionalProperties": False}},
    {"name": "complete_task", "description": "Mark a task done, by id or by words from its title.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}},
                      "required": ["task"], "additionalProperties": False}},
    {"name": "check_email",
     "description": "List recent inbox emails (sender, subject, snippet, id). Defaults to unread "
                    "from the last day; pass a Gmail search query to look for something specific, "
                    "or an empty string for the default.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False}},
    {"name": "read_email", "description": "Read the full text of one email by id.",
     "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}},
                      "required": ["message_id"], "additionalProperties": False}},
    {"name": "draft_reply",
     "description": "Save a reply to an email as a Gmail DRAFT (never sent). Write the full reply "
                    "body as the user would sign it.",
     "input_schema": {"type": "object",
                      "properties": {"message_id": {"type": "string"}, "body": {"type": "string"}},
                      "required": ["message_id", "body"], "additionalProperties": False}},
    {"name": "end_call", "description": "Hang up after you've said goodbye.",
     "input_schema": {"type": "object", "properties": {}, "required": [],
                      "additionalProperties": False}},
]
for _t in TOOLS:
    _t["strict"] = True

KICKOFFS = {
    "morning": "Give the MORNING briefing: important emails that need them, today's training "
               "blocks, open tasks, and suggest today's top three priorities.",
    "late-morning": "Quick LATE-MORNING check-in: on market days, the paper-trading block is "
                    "ending, so remind them to log trades. Mention anything new and important in "
                    "email, then their top priority before lunch.",
    "midday": "MIDDAY check-in: ask what's done and what's left, remind them of this afternoon's "
              "training, and mention anything new and important in email.",
    "evening": "EVENING wrap-up: ask whether today's training happened, mention any important "
               "emails still unanswered, and set tomorrow's top three priorities and training.",
    "incoming": "They just called you. Greet them in a few words and ask what they need. Don't "
                "check email or tasks until they ask.",
}


@dataclass
class Turn:
    text: str
    end_call: bool = False


@dataclass
class Conversation:
    kind: str
    messages: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    first_turn: "Turn | None" = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class Assistant:
    def __init__(self, settings: Settings, tasks: TaskStore, gmail=None, client=None):
        self.s = settings
        self.tasks = tasks
        self.gmail = gmail
        self.client = client or anthropic.Anthropic()
        self.system = SYSTEM_TEMPLATE.format(name=settings.owner_name, plan=settings.plan_text())

    # ---- public -----------------------------------------------------------
    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.s.timezone))

    def start(self, kind: str, extra: str = "") -> tuple[Conversation, Turn]:
        """Open a conversation for a call and produce the first thing to say."""
        conv = Conversation(kind=kind)
        now = self.now()
        instruction = KICKOFFS.get(kind, kind)
        kickoff = (f"[Call connected. It is {now:%A %B %d %Y, %I:%M %p} {self.s.timezone}. "
                   f"{instruction} {extra}]").strip()
        return conv, self.reply(conv, kickoff)

    def reply(self, conv: Conversation, user_text: str) -> Turn:
        with conv.lock:
            conv.messages.append({"role": "user", "content": user_text})
            try:
                return self._run(conv)
            except anthropic.APIError as e:
                log.exception("Claude API error: %s", e)
                # keep history valid: the user turn stays, add a short assistant turn
                conv.messages.append({"role": "assistant",
                                      "content": "Sorry, I had a connection problem."})
                return Turn("Sorry, I'm having trouble connecting right now. Try me again in a minute.")

    # ---- internals --------------------------------------------------------
    def _create(self, messages):
        return self.client.beta.messages.create(
            model=self.s.anthropic_model,
            max_tokens=4000,
            system=self.system,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": self.s.effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def _run(self, conv: Conversation) -> Turn:
        end_call = False
        spoken: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self._create(conv.messages)
            conv.messages.append({"role": "assistant", "content": resp.content})
            spoken += [b.text for b in resp.content if b.type == "text" and b.text.strip()]

            if resp.stop_reason == "refusal":
                return Turn("Sorry, I can't help with that one. Anything else?")
            if resp.stop_reason == "pause_turn":
                continue
            calls = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                break
            results = []
            for call in calls:
                if call.name == "end_call":
                    end_call = True
                out, is_err = self._tool(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": out, "is_error": is_err})
            conv.messages.append({"role": "user", "content": results})
            if end_call:
                # history must end on a valid turn if the call continues somehow
                conv.messages.append({"role": "assistant", "content": "Goodbye."})
                break
        text = " ".join(spoken).strip() or ("Goodbye!" if end_call else "Sorry, could you say that again?")
        return Turn(text=text, end_call=end_call)

    def _tool(self, name: str, args: dict) -> tuple[str, bool]:
        try:
            return self._dispatch(name, args), False
        except Exception as e:  # tool errors go back to Claude, not up the stack
            log.exception("tool %s failed", name)
            return f"Error: {e}", True

    def _dispatch(self, name: str, a: dict) -> str:
        if name == "list_tasks":
            tasks = self.tasks.open_tasks()
            return json.dumps(tasks) if tasks else "No open tasks."
        if name == "add_task":
            t = self.tasks.add(a["title"], a.get("remind_at") or None)
            return f"Added task {t['id']}" + (f" with a reminder call at {t['remind_at']}." if t["remind_at"] else ".")
        if name == "complete_task":
            t = self.tasks.complete(a["task"])
            return f"Marked done: {t['title']}" if t else "No open task matched."
        if name == "end_call":
            return "Hanging up."
        if name in ("check_email", "read_email", "draft_reply"):
            if self.gmail is None:
                return "Gmail is not connected yet, so I can't see email. Tell the user."
            if name == "check_email":
                q = a.get("query") or None
                msgs = self.gmail.list_messages(q) if q else self.gmail.list_messages()
                return json.dumps(msgs) if msgs else "No matching emails."
            if name == "read_email":
                return json.dumps(self.gmail.read_message(a["message_id"]))
            d = self.gmail.create_reply_draft(a["message_id"], a["body"])
            return f"Draft saved in Gmail (not sent) to {d['to']}, subject {d['subject']}."
        raise ValueError(f"unknown tool {name}")

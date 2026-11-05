"""Flask app: Twilio voice webhooks, a protected 'call me now' endpoint and the
scheduler that phones you at check-in and reminder times."""
from __future__ import annotations

import functools
import hmac
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from flask import Flask, Response, abort, request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Gather, VoiceResponse

from .brain import Assistant, Conversation, Turn
from .config import Settings

log = logging.getLogger(__name__)

INLINE_WAIT = 8.0      # seconds to wait for Claude before telling Twilio to hold
HOLD_PAUSE = 2         # seconds per hold loop
MAX_HOLDS = 25         # ~50s of holding before giving up on a turn
CONV_TTL = 2 * 3600


class CallState:
    """In-memory state for live calls, keyed by Twilio CallSid."""

    def __init__(self):
        self.convs: dict[str, Conversation] = {}
        self.pending: dict[str, Future] = {}
        self.silences: dict[str, int] = {}
        self.lock = threading.Lock()

    def put(self, sid: str, conv: Conversation) -> None:
        with self.lock:
            cutoff = time.time() - CONV_TTL
            for old in [k for k, c in self.convs.items() if c.created < cutoff]:
                self.convs.pop(old, None)
                self.pending.pop(old, None)
                self.silences.pop(old, None)
            self.convs[sid] = conv

    def get(self, sid: str) -> Conversation | None:
        with self.lock:
            return self.convs.get(sid)


def create_app(settings: Settings, assistant: Assistant, twilio_client=None) -> Flask:
    app = Flask(__name__)
    state = CallState()
    pool = ThreadPoolExecutor(max_workers=8)
    validator = RequestValidator(settings.twilio_token)
    app.config.update(STATE=state, ASSISTANT=assistant, SETTINGS=settings)

    def url(path: str) -> str:
        return f"{settings.public_base_url}{path}"

    # ---- security --------------------------------------------------------
    def twilio_only(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if settings.validate_twilio:
                full = url(request.full_path.rstrip("?"))
                sig = request.headers.get("X-Twilio-Signature", "")
                if not validator.validate(full, request.form.to_dict(), sig):
                    log.warning("rejected request with bad Twilio signature: %s", full)
                    abort(403)
            return fn(*a, **kw)
        return wrapper

    def twiml(vr: VoiceResponse) -> Response:
        return Response(str(vr), mimetype="text/xml")

    def speak_and_listen(turn: Turn) -> Response:
        vr = VoiceResponse()
        if turn.end_call:
            vr.say(turn.text, voice=settings.voice)
            vr.hangup()
            return twiml(vr)
        g = Gather(input="speech", action=url("/voice/turn"), method="POST",
                   speech_timeout="auto", language="en-US", action_on_empty_result=True)
        g.say(turn.text, voice=settings.voice)
        vr.append(g)
        vr.redirect(url("/voice/turn"), method="POST")  # Gather timed out with nothing heard
        return twiml(vr)

    # ---- placing calls ---------------------------------------------------
    def twilio():
        nonlocal twilio_client
        if twilio_client is None:
            from twilio.rest import Client
            twilio_client = Client(settings.twilio_sid, settings.twilio_token)
        return twilio_client

    def place_call(kind: str, extra: str = "") -> str:
        """Prepare what to say first (so you hear it right away), then dial."""
        conv, first = assistant.start(kind, extra)
        call = twilio().calls.create(to=settings.owner_number, from_=settings.twilio_number,
                                     url=url("/voice/answer"), method="POST")
        conv.first_turn = first
        state.put(call.sid, conv)
        log.info("placed %s call %s", kind, call.sid)
        return call.sid

    app.config["PLACE_CALL"] = place_call

    # ---- routes ----------------------------------------------------------
    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/call-now")
    def call_now():
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not settings.admin_token or not hmac.compare_digest(token, settings.admin_token):
            abort(401)
        kind = request.args.get("kind", "morning")
        return {"call_sid": place_call(kind)}

    @app.post("/voice/answer")
    @twilio_only
    def answer():
        """You picked up a call the assistant placed."""
        sid = request.form.get("CallSid", "")
        conv = state.get(sid)
        if conv is None:
            conv, first = assistant.start("incoming")
            state.put(sid, conv)
        else:
            first = conv.first_turn or Turn("Hi, it's your assistant. What do you need?")
        return speak_and_listen(first)

    @app.post("/voice/incoming")
    @twilio_only
    def incoming():
        """You called the assistant's number."""
        vr = VoiceResponse()
        if request.form.get("From", "") != settings.owner_number:
            vr.say("Sorry, this line is private.", voice=settings.voice)
            vr.hangup()
            return twiml(vr)
        sid = request.form.get("CallSid", "")
        # greet instantly without waiting on Claude; the conversation starts on your first words
        conv = Conversation(kind="incoming")
        state.put(sid, conv)
        return speak_and_listen(Turn(f"Hey {settings.owner_name}, what do you need?"))

    def answer_or_hold(sid: str, holds: int) -> Response:
        fut = state.pending.get(sid)
        if fut is None:
            return speak_and_listen(Turn("Sorry, I lost my train of thought. What was that?"))
        try:
            turn = fut.result(timeout=INLINE_WAIT if holds == 0 else 0.1)
        except TimeoutError:
            if holds >= MAX_HOLDS:
                state.pending.pop(sid, None)
                return speak_and_listen(Turn("Sorry, that's taking too long. Can you ask me again?"))
            vr = VoiceResponse()
            if holds == 0:
                vr.say("One moment.", voice=settings.voice)
            vr.pause(length=HOLD_PAUSE)
            vr.redirect(url(f"/voice/hold?n={holds + 1}"), method="POST")
            return twiml(vr)
        state.pending.pop(sid, None)
        return speak_and_listen(turn)

    @app.post("/voice/turn")
    @twilio_only
    def turn():
        sid = request.form.get("CallSid", "")
        conv = state.get(sid)
        if conv is None:
            conv = Conversation(kind="incoming")
            state.put(sid, conv)
        said = (request.form.get("SpeechResult") or "").strip()
        if not said:
            n = state.silences.get(sid, 0) + 1
            state.silences[sid] = n
            if n >= 3:
                return speak_and_listen(Turn("I'll let you go. Call me back anytime.", end_call=True))
            return speak_and_listen(Turn("Are you still there?"))
        state.silences[sid] = 0
        if not conv.messages:  # an incoming call: give Claude the date/time context first
            now = assistant.now()
            said = (f"[Incoming call from {settings.owner_name}. It is {now:%A %B %d %Y, %I:%M %p} "
                    f"{settings.timezone}.] {said}")
        state.pending[sid] = pool.submit(assistant.reply, conv, said)
        return answer_or_hold(sid, 0)

    @app.post("/voice/hold")
    @twilio_only
    def hold():
        return answer_or_hold(request.form.get("CallSid", ""), int(request.args.get("n", "1")))

    return app

# Phone assistant

A personal assistant you talk to on the phone. It:

- **Calls you** at check-in times (default 7:00am, 11:00am, 12:30pm and 8:00pm Eastern) with a spoken briefing: important email, today's training, open tasks, top priorities.
- **Answers when you call it.** Only your number gets through. Everyone else hears "this line is private."
- **Keeps a task list** ("remind me to call the bank at 3") and **calls you back** at the reminder time.
- **Reads your Gmail and writes reply drafts.** It cannot send email: it only gets read and draft permission, and the code has no send function. Drafts wait in Gmail for you.
- Follows your training plan in [`plan.md`](plan.md). Edit that file to change it.

Claude (`claude-opus-5-5`) does the thinking. Twilio provides the phone line and the voices.

## How it works

```
Twilio number ──webhook──▶ this server (Flask) ──▶ Claude API (tools: tasks, Gmail)
      ▲                         │
      └──── outbound calls ◀────┘  scheduler: check-ins + task reminders
```

Each call is one Claude conversation. Twilio turns your speech into text and posts it to `/voice/turn`. The server gets Claude's reply and Twilio reads it aloud. When a reply takes longer than about 8 seconds, it says "one moment" and holds the line instead of letting Twilio time out.

## Try it without a phone

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # https://console.anthropic.com
python -m assistant.chat morning    # type to it; same brain as the phone
```

## Set up the real thing

1. **Twilio.** Create an account at twilio.com, buy a phone number with voice, and note your Account SID and Auth Token. On a trial account you must first verify your own mobile number under *Verified Caller IDs*.
2. **Hosting.** Deploy this folder with the included `Dockerfile` to any host that stays **always on**, such as a Render paid instance, Railway or Fly.io. Free tiers that go to sleep will miss the scheduled calls. Run exactly **one** instance, or you'll get every call twice.
3. **Settings.** Copy every variable from [`.env.example`](.env.example) into the host's environment settings. Set `PUBLIC_BASE_URL` to the host's https URL and `ADMIN_TOKEN` to a long random string. Never commit real keys.
4. **Point the number at the server.** In Twilio, open *Phone Numbers → your number → Voice*. Set "A call comes in" to Webhook, `POST`, `https://YOUR-HOST/voice/incoming`.
5. **Test a call:**
   ```bash
   curl -X POST "https://YOUR-HOST/call-now?kind=morning" -H "Authorization: Bearer $ADMIN_TOKEN"
   ```
   Your phone should ring within a few seconds.

### Gmail (optional, but you need it for email)

1. In Google Cloud Console, create a project, enable the **Gmail API**, and set up the OAuth consent screen (External, add yourself as a test user). Then create an **OAuth client ID** of type *Desktop app*.
2. On your own computer, run `python scripts/gmail_auth.py CLIENT_ID CLIENT_SECRET` and sign in.
3. Put `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` and the printed `GMAIL_REFRESH_TOKEN` in the host settings.

While the consent screen is in "Testing" mode, Google expires the refresh token after 7 days. To stop that, publish the app; for personal use you can skip Google's verification.

## Things to know

- **Cost.** Expect roughly $1–2 a month for the Twilio number, a few cents per call minute, a few dollars a month for hosting, and Claude API usage. Check current prices.
- **Tasks are saved in `data/tasks.json`.** On hosts with temporary disks, attach a persistent disk at `DATA_DIR` or the list resets when you redeploy.
- **Unanswered calls go to voicemail,** so the briefing gets left as a message.
- **Speed vs. care.** `ASSISTANT_EFFORT=low` keeps replies quick. Use `medium` for more careful answers at the cost of longer pauses.
- **Security.** Every `/voice/*` request has to carry a valid Twilio signature. `/call-now` needs your `ADMIN_TOKEN`. Inbound calls from any number other than `OWNER_PHONE_NUMBER` are refused.
- **WeChat isn't supported.** WeChat has no API for personal accounts.

## Tests

```bash
pip install pytest && python -m pytest -q tests
```

The tests use a fake Claude, a fake Twilio and a fake Gmail, so they need no keys or network.

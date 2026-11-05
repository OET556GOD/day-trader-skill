"""Read-and-draft Gmail access. It can never send: it only holds the compose
scope for drafts, and there is no send function."""
from __future__ import annotations

import base64
import re
from email.message import EmailMessage

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose"]

DEFAULT_QUERY = "in:inbox is:unread newer_than:1d -category:promotions -category:social -category:forums"


def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def _plain_body(payload: dict) -> str:
    """Pull the first text/plain part out of a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    return ""


class Gmail:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, service=None):
        if service is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials(None, refresh_token=refresh_token, client_id=client_id,
                                client_secret=client_secret,
                                token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.svc = service

    def list_messages(self, query: str = DEFAULT_QUERY, limit: int = 10) -> list[dict]:
        res = self.svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        out = []
        for ref in res.get("messages", []):
            msg = self.svc.users().messages().get(
                userId="me", id=ref["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
            h = msg["payload"]["headers"]
            out.append({"id": msg["id"], "thread_id": msg["threadId"], "from": _header(h, "From"),
                        "subject": _header(h, "Subject"), "date": _header(h, "Date"),
                        "snippet": msg.get("snippet", "")})
        return out

    def read_message(self, message_id: str, max_chars: int = 4000) -> dict:
        msg = self.svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        h = msg["payload"]["headers"]
        body = _plain_body(msg["payload"]) or msg.get("snippet", "")
        body = re.sub(r"\n{3,}", "\n\n", body)
        return {"id": msg["id"], "thread_id": msg["threadId"], "from": _header(h, "From"),
                "subject": _header(h, "Subject"), "date": _header(h, "Date"),
                "body": body[:max_chars]}

    def create_reply_draft(self, message_id: str, body: str) -> dict:
        orig = self.svc.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"]).execute()
        h = orig["payload"]["headers"]
        subject = _header(h, "Subject")
        msg = EmailMessage()
        msg["To"] = _header(h, "Reply-To") or _header(h, "From")
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        msg_id = _header(h, "Message-ID")
        if msg_id:
            msg["In-Reply-To"] = msg_id
            msg["References"] = (_header(h, "References") + " " + msg_id).strip()
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = self.svc.users().drafts().create(
            userId="me", body={"message": {"raw": raw, "threadId": orig["threadId"]}}).execute()
        return {"draft_id": draft["id"], "to": msg["To"], "subject": msg["Subject"]}

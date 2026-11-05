"""One-time Gmail sign-in. Prints the GMAIL_REFRESH_TOKEN to put in your settings.

Run this on your own computer (it opens a browser):

    pip install google-auth-oauthlib
    python scripts/gmail_auth.py CLIENT_ID CLIENT_SECRET

The token only allows reading mail and creating drafts. It cannot send.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose"]

if len(sys.argv) != 3:
    sys.exit(__doc__)

flow = InstalledAppFlow.from_client_config(
    {"installed": {"client_id": sys.argv[1], "client_secret": sys.argv[2],
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                   "token_uri": "https://oauth2.googleapis.com/token",
                   "redirect_uris": ["http://localhost"]}},
    SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nGMAIL_REFRESH_TOKEN=" + creds.refresh_token)
print("Keep this secret: put it in your host's environment settings, not in git.")

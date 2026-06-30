"""HTML for the logged-out auth screens (magic-link sign in) + the account stub.

Builders only — no routes (the caller mounts them). They reuse the shared `page()` helper
and CSS conventions from templates.py, so they return an `HTMLResponse` exactly like the
view functions in webui.py do (a route does `return authui.login_page()`).
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse

from .templates import esc, page

# The shared CSS styles input[type=text]/[type=search]; an <input type=email> needs the same
# look applied inline (we can't edit the shared stylesheet here).
_EMAIL_INPUT = (
    "width:100%;padding:.8rem 1rem;font-size:1.02rem;border:1px solid var(--line-2);"
    "border-radius:12px;background:var(--panel);box-shadow:var(--shadow);font-family:inherit"
)


def login_page(error: str | None = None) -> HTMLResponse:
    """The sign-in screen: an email form that POSTs to /auth/request."""
    err = (
        f'<div class="answer" style="border-left-color:#d64545">{esc(error)}</div>'
        if error
        else ""
    )
    body = (
        "<p class=lead>Sign in with a magic link — enter your email and we'll send you a "
        "one-tap link. No password to remember.</p>"
        + err
        + '<form method=post action="/auth/request" class="narrow">'
        f'<input type=email name=email placeholder="you@example.com" '
        f'autocomplete=email required autofocus style="{_EMAIL_INPUT}">'
        '<div class=row style="margin-top:.6rem"><button>Send magic link</button></div>'
        "</form>"
    )
    return page("Sign in", body)


def check_email_page(email: str) -> HTMLResponse:
    """Confirmation shown after a magic link has been requested."""
    body = (
        '<div class="answer">📬 <b>Check your email.</b><br>'
        f"We sent a sign-in link to <b>{esc(email)}</b>. It expires in 15 minutes — open it "
        "on this device to finish signing in.</div>"
        '<p class=muted>Didn\'t get it? Check your spam folder, or '
        '<a href="/login">try a different email</a>.</p>'
    )
    return page("Check your email", body)


def account_page(email: str) -> HTMLResponse:
    """A small account/settings stub: shows the signed-in email + a log-out control."""
    body = (
        '<div class="stats"><div class="stat"><b>Signed in</b>'
        f"<span>{esc(email)}</span></div></div>"
        "<p class=lead>You're signed in to your private bookmark brain.</p>"
        '<form method=post action="/auth/logout">'
        '<button class="ghost">Log out</button></form>'
    )
    return page("Account", body)

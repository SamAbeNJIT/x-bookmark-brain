"""Outbound email (magic-link sign-in) via Amazon SES.

If no SES sender is configured (local dev), the link is logged to the console instead of sent —
so sign-in works without any email infrastructure. In production, set SES_SENDER to a verified
SES identity and requests go out as real email.
"""

from __future__ import annotations

_SUBJECT = "Your x-bookmark-brain sign-in link"


def _body_html(link: str) -> str:
    return (
        "<p>Click to sign in to x-bookmark-brain:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        "<p>This link expires in 15 minutes. If you didn't request it, ignore this email.</p>"
    )


def send_login_link(email: str, link: str, *, ses_sender: str | None, region: str) -> None:
    """Email the magic link via SES, or log it to the console if SES isn't configured."""
    if not ses_sender:
        print(f"[auth] magic link for {email}: {link}", flush=True)
        return
    import boto3  # pragma: no cover - needs AWS

    boto3.client("ses", region_name=region).send_email(  # pragma: no cover
        Source=ses_sender,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": _SUBJECT},
            "Body": {"Html": {"Data": _body_html(link)}},
        },
    )

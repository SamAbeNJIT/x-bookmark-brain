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


def send_owner_alert(subject: str, body: str, *, ses_sender: str | None,
                     owner_email: str | None, region: str) -> None:
    """Fire-and-forget ops alert to the owner (signups, purchases). Never raises: alerts must
    never break the flow that triggered them. Unset owner_email/sender → console log (local)."""
    try:
        if not (ses_sender and owner_email):
            print(f"[alert] {subject}: {body}", flush=True)
            return
        import boto3

        boto3.client("ses", region_name=region).send_email(
            Source=ses_sender,
            Destination={"ToAddresses": [owner_email]},
            Message={"Subject": {"Data": subject},
                     "Body": {"Text": {"Data": body}}},
        )
    except Exception as e:  # pragma: no cover - best-effort by design
        print(f"[alert] send failed ({type(e).__name__}): {subject}", flush=True)


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

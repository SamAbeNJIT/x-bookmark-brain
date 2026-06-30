"""Envelope-free KMS encryption for small secrets (the X OAuth tokens).

OAuth token JSON is well under KMS's 4KB direct-encrypt limit, so we use `kms:Encrypt` /
`kms:Decrypt` directly (no data-key envelope needed). Ciphertext is stored base64 with a
``kms:`` marker so a value can be told apart from legacy plaintext — making the rollout
backward-compatible (old plaintext rows still decrypt to themselves).

If no key is configured (local dev), everything is a passthrough: plaintext in, plaintext out.
The `context` (tenant id) is bound as the KMS EncryptionContext, so a ciphertext can only be
decrypted for the same tenant — a cryptographic tie that also shows up in CloudTrail.
"""

from __future__ import annotations

import base64

_MARKER = "kms:"


def _client(region: str):  # pragma: no cover - needs AWS
    import boto3

    return boto3.client("kms", region_name=region)


def encrypt(plaintext: str, key_id: str | None, region: str, context: dict[str, str]) -> str:
    """Encrypt with KMS (returns 'kms:<b64>'), or return plaintext unchanged if no key is set."""
    if not key_id:
        return plaintext
    resp = _client(region).encrypt(
        KeyId=key_id, Plaintext=plaintext.encode(), EncryptionContext=context
    )  # pragma: no cover
    return _MARKER + base64.b64encode(resp["CiphertextBlob"]).decode()  # pragma: no cover


def decrypt(value: str, region: str, context: dict[str, str]) -> str:
    """Decrypt a 'kms:<b64>' value; pass through anything without the marker (legacy plaintext)."""
    if not value.startswith(_MARKER):
        return value
    blob = base64.b64decode(value[len(_MARKER):])  # pragma: no cover
    resp = _client(region).decrypt(CiphertextBlob=blob, EncryptionContext=context)  # pragma: no cover
    return resp["Plaintext"].decode()  # pragma: no cover

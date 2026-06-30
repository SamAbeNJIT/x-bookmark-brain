import base64
import hashlib

from xbb import xauth


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = xauth.make_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge  # base64url, unpadded


def test_authorize_url_has_pkce_params():
    url = xauth.authorize_url("CID", "http://127.0.0.1:8000/oauth/callback", "st8", "chal")
    assert url.startswith(xauth.AUTHORIZE_URL)
    for frag in ["response_type=code", "client_id=CID", "code_challenge=chal",
                 "code_challenge_method=S256", "state=st8", "bookmark.read"]:
        assert frag in url

"""Mint a GitHub App installation token on the bridge runner (kata jibot-code#q4av).

The App's private key lives on the bridge runner's filesystem and is never an
Actions secret: an organisation secret is readable by every workflow in the
selected repos, including the copy a pull request rewrote. It is also never
read into this process. `openssl dgst -sign <path>` opens the file itself, so
the key material does not pass through Python, an environment variable or a
log line; this module handles the path and the signature only.

The token it returns is scoped to ONE repository and ONE permission
(`checks: write`), whatever the installation was granted, and lives an hour.
"""

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
DEFAULT_KEY_PATH = "~/.mujin/review-evidence.pem"


class TokenError(Exception):
    pass


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_jwt(app_id, key_path, now=None, sign=None):
    """An RS256 JWT for the App. `sign(message_bytes) -> signature_bytes`."""
    now = int(now if now is not None else time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"},
                                separators=(",", ":")).encode())
    # iat 60 s in the past absorbs clock drift; GitHub caps exp at 10 minutes.
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540,
                                  "iss": str(app_id)},
                                 separators=(",", ":")).encode())
    message = ("%s.%s" % (header, payload)).encode("ascii")
    signature = (sign or _openssl_signer(key_path))(message)
    return "%s.%s" % (message.decode("ascii"), _b64url(signature))


def _openssl_signer(key_path):
    path = os.path.expanduser(key_path)

    def sign(message):
        mode = _mode(path)
        if mode is None:
            raise TokenError("the App key is not at %s" % path)
        if mode & 0o077:
            # A key other accounts can read is a key the gate account can
            # read. Refuse to use it rather than paper over the setup error.
            raise TokenError("the App key at %s is mode %o; it must be 0600"
                             % (path, mode))
        done = subprocess.run(["openssl", "dgst", "-sha256", "-sign", path],
                              input=message, capture_output=True)
        if done.returncode != 0 or not done.stdout:
            # stderr from openssl names the path and the failure, never the key.
            raise TokenError("openssl could not sign: %s"
                             % done.stderr.decode("utf-8", "replace").strip()[:200])
        return done.stdout
    return sign


def _mode(path):
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return None


def installation_token(app_id, installation_id, repo_name, key_path=None,
                       opener=None):
    """A one-hour token for `repo_name` (the bare name, no owner), checks:write."""
    jwt = build_jwt(app_id, key_path or os.environ.get("MUJIN_APP_KEY",
                                                        DEFAULT_KEY_PATH))
    body = json.dumps({"repositories": [repo_name],
                       "permissions": {"checks": "write"}}).encode()
    req = urllib.request.Request(
        "%s/app/installations/%s/access_tokens" % (API, installation_id),
        data=body, method="POST",
        headers={"Authorization": "Bearer %s" % jwt,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=30) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise TokenError("GitHub refused the App token request: HTTP %s" % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TokenError("could not reach GitHub for an App token: %s"
                         % type(exc).__name__)
    token = doc.get("token")
    if not isinstance(token, str) or not token:
        raise TokenError("GitHub's reply carried no token")
    return token

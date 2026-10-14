"""Byte-exact request material captured from the node-runner client.

Header order matters: it is the order observed in the capture and is preserved
verbatim when the request is built (see itl_client.py).
"""

HOST = "node-runner-gilt.vercel.app"
BASE_URL = f"https://{HOST}"
ORIGIN = BASE_URL

# ---------------------------------------------------------------- static UA --
# Shared headers, byte-exact from the capture. Sent on every request.
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
ACCEPT_LANGUAGE = "en-GB,en;q=0.9"
ACCEPT_ENCODING = "gzip, deflate, br, zstd"
CONNECTION = "keep-alive"

# curl_cffi impersonation profile whose TLS/JA3 + HTTP2 fingerprint matches
# the captured client (iOS Safari 18).
IMPERSONATE = "safari_ios"

# The leaderboard is a server-rendered HTML page. It is public -- no Authorization
# header is sent for it -- but the session cookie still travels along so the server
# can mark the signed-in player's own row with data-you="true".
LEADERBOARD_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

# ------------------------------------------------------------------ tokens --
# Kein Token im Code. Der Session-Token kommt aus --jwt / --url, einem
# gespeicherten Profil (~/.itl_session.json) oder der Umgebungsvariable ITL_SESSION.
#
# These are two-segment tokens (base64url payload + base64url HMAC signature) --
# NOT standard three-segment JWTs. The game calls them JWTs but omits the header
# segment; the server accepts them as-is. `token_health()` knows about this shape.
SESSION_ENV_VAR = "ITL_SESSION"

# Roles:
#   session token -> arrives as ?s=... on /play/<n>, stored as sessionStorage
#                    ["itl.session"], sent as `Authorization: Bearer <token>`
#   runToken      -> returned by POST /api/run/start, sent only in the body of
#                    POST /api/run/complete

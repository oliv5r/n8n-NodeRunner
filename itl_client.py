"""HTTP layer: byte-exact header construction and the three API calls."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests as cffi_requests
from curl_cffi.const import CurlHttpVersion

import itl_config as cfg


class ItlError(RuntimeError):
    """Raised when the server answers with a non-2xx status."""

    def __init__(self, status: int, body: str, where: str) -> None:
        detail = f"HTTP {status} -- {body[:300]}" if status else body
        super().__init__(f"{where}: {detail}")
        self.status = status
        self.body = body
        self.where = where


# ------------------------------------------------------------------ helpers --

def b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def token_parts(token: str) -> tuple[str, str] | None:
    """Split into (payload_segment, signature_segment).

    These tokens have two segments -- payload.signature -- not the usual three.
    The game calls them JWTs but omits the header segment entirely.
    """
    parts = token.strip().split(".")
    if len(parts) == 3:
        return parts[1], parts[2]        # standard JWT, in case that changes
    if len(parts) == 2:
        return parts[0], parts[1]        # the shape this server actually issues
    return None


def jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode the payload segment without verifying anything. None if malformed."""
    parts = token_parts(token)
    if parts is None:
        return None
    try:
        return json.loads(b64url_decode(parts[0]))
    except Exception:
        return None


def token_health(token: str) -> str:
    """Human-readable state of a token, so the tool can warn before using it."""
    parts = token_parts(token)
    if parts is None:
        return f"MALFORMED ({len(token.strip().split('.'))} segments)"

    _, signature = parts
    payload = jwt_payload(token)
    notes: list[str] = []
    if len(signature) != 43:
        notes.append(f"signature {len(signature)} chars (expected 43)")
    if payload is None:
        notes.append("payload does not decode")
        return "; ".join(notes)

    exp = payload.get("exp")
    if exp:
        # iat/exp in this system are milliseconds, not seconds.
        exp_s = exp / 1000
        if exp_s < time.time():
            notes.append(f"ABGELAUFEN {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(exp_s))} UTC")
        else:
            hours = (exp_s - time.time()) / 3600
            notes.append(f"gueltig noch {hours:.1f} h "
                         f"(bis {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(exp_s))} UTC)")
    return "; ".join(notes) if notes else "ok"


# ------------------------------------------------------------------ session --

@dataclass
class Session:
    jwt: str
    referer_level: int = 0
    source: str = "unknown"
    profile: dict[str, Any] = field(default_factory=dict)

    @property
    def cookie(self) -> str:
        return f"itl_session={self.jwt}"

    @property
    def referer(self) -> str:
        return f"{cfg.ORIGIN}/play/{self.referer_level}"

    @property
    def user_id(self) -> str | None:
        return (jwt_payload(self.jwt) or {}).get("id")

    def describe(self) -> str:
        p = jwt_payload(self.jwt) or {}
        return f"source={self.source} level={self.referer_level} id={p.get('id')} [{token_health(self.jwt)}]"


def session_from_url(url: str) -> Session | None:
    """Pull the session JWT out of a /play/<n>?s=... launch URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("s", "session", "token"):
        if qs.get(key):
            jwt = qs[key][0]
            level = 0
            path_bits = [b for b in parsed.path.split("/") if b]
            if len(path_bits) >= 2 and path_bits[-2] == "play" and path_bits[-1].isdigit():
                level = int(path_bits[-1])
            return Session(jwt=jwt, referer_level=level, source="url")
    return None


def load_session(profile_path: str, url: str | None = None) -> Session:
    """Resolve the session token, in order of preference:

    1. an explicit URL or bare token (--url / --jwt)
    2. a previously stored profile
    3. the ITL_SESSION environment variable
    """
    if url:
        sess = session_from_url(url)
        if sess:
            return sess
        # A bare token pasted straight in.
        if not url.startswith("http") and token_parts(url):
            return Session(jwt=url.strip(), source="--jwt")

    if os.path.exists(profile_path):
        try:
            with open(profile_path) as fh:
                saved = json.load(fh)
        except (OSError, json.JSONDecodeError):
            saved = {}
        if saved.get("jwt"):
            return Session(
                jwt=saved["jwt"],
                referer_level=saved.get("referer_level", 0),
                source="gespeichertes Profil",
                profile=saved,
            )

    env_token = os.environ.get(cfg.SESSION_ENV_VAR, "").strip()
    if env_token:
        return Session(jwt=env_token, source=f"${cfg.SESSION_ENV_VAR}")

    raise ItlError(
        0,
        "kein Session-Token gefunden. Mit --jwt '<token>', --url '<launch-url>' "
        f"oder ${cfg.SESSION_ENV_VAR} uebergeben.",
        "Session",
    )


# ------------------------------------------------------------------- client --

class ItlClient:
    """Thin wrapper over the three endpoints, with capture-exact headers."""

    def __init__(self, session: Session, verbose: bool = False, http_version: str = "1.1") -> None:
        self.session = session
        self.verbose = verbose
        # The capture is HTTP/1.1. Forcing it also keeps header order stable:
        # over cleartext HTTP, curl would otherwise negotiate an h2c upgrade and
        # reorder/inject headers (Upgrade, HTTP2-Settings).
        self.http_version = {
            "1.0": CurlHttpVersion.V1_0,
            "1.1": CurlHttpVersion.V1_1,
            "2": CurlHttpVersion.V2_0,
            "3": CurlHttpVersion.V3,
        }[http_version]
        self._http = cffi_requests.Session(impersonate=cfg.IMPERSONATE)

    # -- header construction -------------------------------------------------
    # A list of tuples, not a dict: the capture's header ORDER is part of what we
    # are reproducing, and dicts (plus most client wrappers) would reorder it.
    def _headers(self, level: int, body: bytes | None) -> list[tuple[str, str]]:
        headers = [("Host", cfg.HOST)]
        if body is not None:
            headers.append(("Content-Type", "application/json"))
        headers.append(("Authorization", f"Bearer {self.session.jwt}"))
        headers += [
            ("Origin", cfg.ORIGIN),
            ("Referer", f"{cfg.ORIGIN}/play/{level}"),
            ("User-Agent", cfg.UA),
            ("Accept-Language", cfg.ACCEPT_LANGUAGE),
            ("Accept-Encoding", cfg.ACCEPT_ENCODING),
            ("Cookie", self.session.cookie),
            ("Connection", cfg.CONNECTION),
        ]
        if body is not None:
            headers.append(("Content-Length", str(len(body))))
        return headers

    # -- request plumbing ----------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any], level: int, where: str) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode()
        url = f"{cfg.BASE_URL}{path}"
        headers = self._headers(level, body)

        if self.verbose:
            print(f"\n--- {where}")
            print(f"POST {url}")
            for name, value in headers:
                shown = value
                if name == "Authorization" and len(shown) > 40:
                    shown = shown[:28] + "..." + shown[-8:]
                print(f"{name}: {shown}")
            print(f"\n{body.decode()}")

        resp = self._http.request(
            "POST",
            url,
            data=body,
            headers=headers,
            http_version=self.http_version,
            default_headers=False,
        )
        return self._parse(resp, where)

    def leaderboard_html(self) -> str:
        """GET /leaderboard -- public HTML, no Authorization header."""
        url = f"{cfg.BASE_URL}/leaderboard"
        headers = [
            ("Host", cfg.HOST),
            ("User-Agent", cfg.UA),
            ("Accept", cfg.LEADERBOARD_ACCEPT),
            ("Accept-Language", cfg.ACCEPT_LANGUAGE),
            ("Accept-Encoding", cfg.ACCEPT_ENCODING),
            ("Cookie", self.session.cookie),
            ("Connection", cfg.CONNECTION),
        ]
        if self.verbose:
            print("\n--- GET /leaderboard")
            print(f"GET {url}")
            for name, value in headers:
                print(f"{name}: {value}")

        resp = self._http.request(
            "GET",
            url,
            headers=headers,
            http_version=self.http_version,
            default_headers=False,
        )
        if self.verbose:
            print(f"\n<-- HTTP {resp.status_code} ({len(resp.text)} bytes)")
        if not (200 <= resp.status_code < 300):
            raise ItlError(resp.status_code, resp.text[:300], "GET /leaderboard")
        return resp.text

    def _get(self, path: str, level: int, where: str) -> dict[str, Any]:
        url = f"{cfg.BASE_URL}{path}"
        headers = self._headers(level, None)
        if self.verbose:
            print(f"\n--- {where}")
            print(f"GET {url}")
            for name, value in headers:
                print(f"{name}: {value}")
        resp = self._http.request(
            "GET",
            url,
            headers=headers,
            http_version=self.http_version,
            default_headers=False,
        )
        return self._parse(resp, where)

    def _parse(self, resp: Any, where: str) -> dict[str, Any]:
        text = resp.text
        if self.verbose:
            print(f"\n<-- HTTP {resp.status_code}")
            print(text[:600])
        if not (200 <= resp.status_code < 300):
            raise ItlError(resp.status_code, text, where)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ItlError(resp.status_code, f"invalid JSON: {text[:300]}", where) from exc

    # -- endpoints ----------------------------------------------------------
    def me(self) -> dict[str, Any]:
        return self._get("/api/me", self.session.referer_level, "GET /api/me")

    def start(self, level: int) -> dict[str, Any]:
        return self._post("/api/run/start", {"level": level}, level, f"POST /api/run/start (level {level})")

    def complete(self, run_token: str, client_ms: int, level: int) -> dict[str, Any]:
        return self._post(
            "/api/run/complete",
            {"runToken": run_token, "clientMs": client_ms},
            level,
            f"POST /api/run/complete (clientMs={client_ms})",
        )

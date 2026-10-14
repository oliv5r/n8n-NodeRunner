#!/usr/bin/env python3
"""Tests for the itl client. Run: python3 test_itl.py

Everything runs against a local mock -- no traffic reaches the real host.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import pty
import re
import select
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CAPTURE_ORDER = [
    "Host", "Content-Type", "Authorization", "Origin", "Referer", "User-Agent",
    "Accept-Language", "Accept-Encoding", "Cookie", "Connection", "Content-Length",
]

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}" + (f"\n         {detail}" if detail else ""))


# ------------------------------------------------------------------- mock ----

SEEN: list[tuple] = []


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _run_token(level: int) -> tuple[str, int]:
    t0 = int(time.time() * 1000)
    payload = {"rid": "11111111-1111-1111-1111-111111111111",
               "uid": "c2:00000000-0000-0000-0000-000000000000",
               "lvl": level, "t0": t0, "k": "run", "iat": t0, "exp": t0 + 1800000}
    return f"{_b64({'alg': 'HS256'})}.{_b64(payload)}.{'x' * 43}", t0


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, obj, code=200) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._body()
        SEEN.append(("GET", self.path, list(self.headers.keys()), b""))
        if self.path == "/api/me":
            # The nested shape the real endpoint returns.
            return self._send({
                "user": {"id": "c2:00000000-0000-0000-0000-000000000000",
                         "displayName": "Max Mustermann", "email": None,
                         "provider": "c2", "trust": "verified"},
                "progress": {
                    "levels": [
                        {"level": 0, "id": "level-0", "label": "Level 0",
                         "title": "How to play", "done": True, "unlocked": True,
                         "next": False, "completions": 2, "attemptsLeft": 3,
                         "bestMs": 75298},
                        {"level": 1, "id": "autonomous-agent",
                         "title": "Autonomous agent", "done": False, "unlocked": True,
                         "next": True, "completions": 0, "attemptsLeft": 5,
                         "bestMs": None},
                    ],
                    "nextLevel": 1, "completedCount": 1, "totalLevels": 2,
                    "codesLeft": 3, "totalCodes": 10,
                },
            })
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        body = self._body()
        SEEN.append(("POST", self.path, list(self.headers.keys()), body))
        if self.path == "/api/run/start":
            token, t0 = _run_token(json.loads(body)["level"])
            return self._send({"status": "started", "runToken": token,
                               "serverStart": t0, "expiresInMs": 1800000,
                               "attemptsLeft": 5})
        if self.path == "/api/run/complete":
            sent = json.loads(body)
            return self._send({"status": "recorded", "level": 0,
                               "durationMs": sent["clientMs"], "bestMs": sent["clientMs"],
                               "isBest": True, "completions": 1, "attemptsLeft": 4,
                               "nextLevel": 1, "completedCount": 1, "totalLevels": 7,
                               "rank": None, "rankedOutOf": None})
        self._send({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


def start_mock(port: int):
    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def point_at(port: int):
    import itl_config as cfg
    cfg.HOST = f"127.0.0.1:{port}"
    cfg.BASE_URL = f"http://127.0.0.1:{port}"
    cfg.ORIGIN = f"http://127.0.0.1:{port}"


def fake_jwt() -> str:
    """A two-segment token, the shape this server actually issues."""
    return (_b64({"id": "c2:00000000-0000-0000-0000-000000000000", "k": "session",
                  "exp": int((time.time() + 3600) * 1000)})
            + "." + "z" * 43)


# ------------------------------------------------------------------ tests ----

def test_time_parsing() -> None:
    import itl_time as t
    print("\n[time parsing]")
    good = {"01:15:298": 75298, "0:00:500": 500, "00:01": 1000, "1:15:298": 75298,
            "75.298": 75298, "01:15:29": 75290, "00:00:001": 1, "10:00:000": 600000,
            "1:5:5": 65500, "00:59:999": 59999}
    for raw, expected in good.items():
        try:
            got = t.parse_duration(raw)
        except t.TimeFormatError as exc:
            check(f"{raw!r} -> {expected}", False, str(exc))
            continue
        check(f"{raw!r} -> {got}", got == expected, f"expected {expected}")

    for raw in ("", "abc", "00:00:000", "00:75:000", "1:2:3456", "::"):
        try:
            t.parse_duration(raw)
            check(f"{raw!r} rejected", False, "was accepted")
        except t.TimeFormatError:
            check(f"{raw!r} rejected", True)

    check("format_duration round-trips",
          t.format_duration(75298) == "01:15:298")


def test_header_order() -> None:
    print("\n[header order]")
    import itl_client as cm
    client = cm.ItlClient(cm.Session(jwt=fake_jwt(), source="test"))
    body = b'{"level":0}'
    got = [k for k, _ in client._headers(0, body)]
    check("POST header order matches capture", got == CAPTURE_ORDER,
          f"\n         got:      {got}\n         expected: {CAPTURE_ORDER}")

    get_headers = [k for k, _ in client._headers(0, None)]
    expected_get = [h for h in CAPTURE_ORDER if h not in ("Content-Type", "Content-Length")]
    check("GET header order matches capture (no body headers)",
          get_headers == expected_get,
          f"\n         got:      {get_headers}\n         expected: {expected_get}")


def test_token_health() -> None:
    print("\n[token health]")
    import itl_client as cm

    # These are two-segment tokens (payload.signature), not three-segment JWTs.
    token = fake_jwt()
    parts = cm.token_parts(token)
    check("token splits into 2 segments", parts is not None)
    check("signature is 43 chars", parts is not None and len(parts[1]) == 43)
    payload = cm.jwt_payload(token) or {}
    check("payload decodes", payload.get("k") == "session", str(payload))
    check("reports a validity window", "gueltig noch" in cm.token_health(token))

    check("two-segment token is not called malformed",
          "MALFORMED" not in cm.token_health(token))
    check("three-segment JWT still supported",
          cm.token_parts("a.b.c") == ("b", "c"))
    check("garbage flagged as malformed",
          "MALFORMED" in cm.token_health("not-a-token"))
    check("expired token flagged",
          "ABGELAUFEN" in cm.token_health(
              _b64({"exp": 1_000_000_000_000}) + "." + "z" * 43))
    check("well-formed token reports ok", "gueltig" in cm.token_health(fake_jwt()))


def test_me_shape() -> None:
    print("\n[api/me shape]")
    import itl_app as app

    nested = {"user": {"id": "u1", "displayName": "Max Mustermann"},
              "progress": {"levels": [{"level": 0, "title": "How to play",
                                      "bestMs": 75298, "done": True},
                                     {"level": 1, "title": "Autonomous agent",
                                      "bestMs": None, "done": False}],
                           "totalLevels": 7, "completedCount": 1, "nextLevel": 1}}
    user, progress = app.split_me(nested)
    check("splits user out of the nested response",
          user["displayName"] == "Max Mustermann")
    check("splits progress out", progress["totalLevels"] == 7)
    check("extracts best times per level", app.load_best(nested) == {0: 75298})
    check("extracts level titles",
          app.level_titles(nested)[1] == "Autonomous agent")
    check("skips levels with no best time",
          1 not in app.load_best(nested))

    # A flat response must still work, so an older shape cannot break the tool.
    flat = {"id": "u1", "displayName": "Max Mustermann", "totalLevels": 7,
            "bestMs": {"0": 75298}}
    fuser, fprogress = app.split_me(flat)
    check("tolerates a flat response", fuser["displayName"] == "Max Mustermann")
    check("flat bestMs still parsed", app.load_best(flat) == {0: 75298})

    check("empty progress is safe", app.load_best({"user": {}, "progress": {}}) == {})
    check("missing levels list is safe",
          app.level_titles({"user": {}, "progress": {"levels": None}}) == {})


def test_requests_byte_exact(port: int) -> None:
    print("\n[requests]")
    import itl_client as cm
    import itl_app as app

    SEEN.clear()
    client = cm.ItlClient(cm.Session(jwt=fake_jwt(), source="test"))
    client.me()
    app.run_once(client, 0, 600, assume_yes=True)

    by_path = {p: (m, h, b) for m, p, h, b in SEEN}
    for path in ("/api/run/start", "/api/run/complete"):
        check(f"{path} header order byte-exact",
              by_path[path][1] == CAPTURE_ORDER, str(by_path[path][1]))

    check("start body is exactly {\"level\":0}", by_path["/api/run/start"][2] == b'{"level":0}')

    complete = json.loads(by_path["/api/run/complete"][2])
    check("complete carries the runToken from start",
          complete["runToken"].startswith("eyJ") and complete["runToken"].count(".") == 2)
    check("complete clientMs equals the requested time", complete["clientMs"] == 600)
    check("runToken is sent only in the body, never as a header",
          all("runToken" not in "".join(h) for _, _, h, _ in SEEN))

    # Only headers the capture actually contains may appear -- in particular no
    # sec-fetch-*, priority, accept or Upgrade injected by the impersonate profile.
    allowed = {h.lower() for h in CAPTURE_ORDER}
    extra = [h for h in by_path["/api/run/start"][1] if h.lower() not in allowed]
    check("no headers beyond the capture's set", not extra, f"unexpected: {extra}")


def test_timing_accuracy() -> None:
    print("\n[timing]")
    import itl_time as t
    deltas = []
    for _ in range(60):
        deadline = time.perf_counter() + 0.008
        deltas.append((t.wait_until(deadline) - deadline) * 1000)
    check(f"wait_until mean drift {statistics.mean(deltas):+.3f} ms (want < 1 ms)",
          abs(statistics.mean(deltas)) < 1.0)
    check(f"wait_until max drift {max(deltas):+.3f} ms (want < 5 ms)", max(deltas) < 5.0)


def test_session_loading() -> None:
    print("\n[session loading]")
    import itl_client as cm
    token = fake_jwt()
    sess = cm.session_from_url(f"https://node-runner-gilt.vercel.app/play/3?s={token}")
    check("extracts jwt from launch URL", sess is not None and sess.jwt == token)
    check("picks up the level from the path", sess is not None and sess.referer_level == 3)

    # A bare token is not a URL, so it goes through load_session rather than
    # session_from_url (which only parses launch URLs).
    bare = cm.load_session("/nonexistent-profile.json", token)
    check("load_session accepts a bare token", bare.jwt == token)
    check("bare token is labelled --jwt", bare.source == "--jwt")

    # With no arguments the token comes from $ITL_SESSION.
    import itl_config as cfg
    saved_env = os.environ.pop(cfg.SESSION_ENV_VAR, None)
    os.environ[cfg.SESSION_ENV_VAR] = token
    env_sess = cm.load_session("/nonexistent-profile.json", None)
    check("falls back to $ITL_SESSION", env_sess.jwt == token)
    del os.environ[cfg.SESSION_ENV_VAR]
    try:
        cm.load_session("/nonexistent-profile.json", None)
        check("no token at all raises", False, "was accepted")
    except cm.ItlError:
        check("no token at all raises", True)
    if saved_env is not None:
        os.environ[cfg.SESSION_ENV_VAR] = saved_env

    # A stored profile is used when no token is passed explicitly.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"jwt": "stored.token", "referer_level": 0}, fh)
        stored_path = fh.name
    check("stored profile wins when present",
          cm.load_session(stored_path, None).jwt == "stored.token")
    os.unlink(stored_path)

    check("rejects a URL with no token",
          cm.session_from_url("https://node-runner-gilt.vercel.app/play/0") is None)


def test_menu(keys: list[bytes], label: str, expected: str) -> None:
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "import itl_ui as ui\n"
        "idx = ui.choose('Pick:', ['zero','one','two','three'])\n"
        "sys.stdout.write('\\nRESULT=%%r\\n' %% idx); sys.stdout.flush()\n"
        % os.path.dirname(os.path.abspath(__file__))
    )
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, "-c", script])

    buf = b""

    def pump(seconds: float) -> None:
        nonlocal buf
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk

    while b"(arrows / enter" not in buf:
        pump(0.2)
        if len(buf) > 200_000:
            break
    for key in keys:
        os.write(fd, key)
        pump(0.35)

    match = re.search(rb"RESULT=(\S+)", buf)
    got = match.group(1).decode() if match else "NONE"
    check(f"menu: {label} -> {got}", got == expected, f"expected {expected}")


# A trimmed but structurally faithful slice of the real /leaderboard markup.
LEADERBOARD_FIXTURE = """<!DOCTYPE html><html><body><main class="hub">
<h1><span>Leader<em>board</em></span></h1>
<p class="sub">Best time per player, per level.</p>
<section class="board-section">
  <h2 class="board-title">Overall<p class="board-note">Total of every best time.</p></h2>
  <ol class="board">
    <li class="board-row" data-you="false"><span class="board-rank">1</span>
      <span class="board-who"><span class="board-name">Erika M.</span></span>
      <span class="board-time">4:58.520</span></li>
    <li class="board-row" data-you="true"><span class="board-rank">2</span>
      <span class="board-who"><span class="board-name">Max Mustermann</span></span>
      <span class="board-time">5:26.789</span></li>
  </ol>
</section>
<section class="board-section">
  <h2 class="board-title">Level 1<span class="board-title-sub">Autonomous agent</span></h2>
  <ol class="board">
    <li class="board-row" data-you="false"><span class="board-rank">1</span>
      <span class="board-who"><span class="board-name">Jonas K.</span></span>
      <span class="board-time">0:15.079</span></li>
  </ol>
</section>
</main></body></html>"""


def test_leaderboard_parsing() -> None:
    import itl_leaderboard as lb

    print("\n[leaderboard]")
    sections = lb.parse_leaderboard(LEADERBOARD_FIXTURE)
    check("finds both sections", len(sections) == 2, f"got {[s.title for s in sections]}")
    if len(sections) != 2:
        return

    overall, level1 = sections
    check("parses section title", overall.title == "Overall")
    check("parses subtitle", level1.subtitle == "Autonomous agent")
    check("parses note", "Total of every best time" in overall.note)
    check("parses all rows", len(overall.rows) == 2)
    check("parses rank/name/time",
          (overall.rows[0].rank, overall.rows[0].name, overall.rows[0].time_text)
          == (1, "Erika M.", "4:58.520"))
    check("reads data-you=true", overall.rows[1].is_you is True)
    check("reads data-you=false", overall.rows[0].is_you is False)
    check("you_row finds the marked row",
          overall.you_row is not None and overall.you_row.name == "Max Mustermann")
    check("no you_row when nobody is marked", level1.you_row is None)

    check("parses '4:58.520' -> 298520 ms", lb.parse_board_time("4:58.520") == 298520)
    check("parses '0:15.079' -> 15079 ms", lb.parse_board_time("0:15.079") == 15079)
    check("rejects junk times", lb.parse_board_time("n/a") is None)
    check("row.time_ms works", overall.rows[0].time_ms == 298520)

    # A name containing markup-ish characters must survive escaping.
    tricky = LEADERBOARD_FIXTURE.replace("Erika M.", "A &amp; B &quot;Q&quot;")
    got = lb.parse_leaderboard(tricky)[0].rows[0].name
    check("unescapes entities in names", got == 'A & B "Q"', repr(got))

    # Duplicate markup (as the real RSC payload contains) must collapse.
    dup = LEADERBOARD_FIXTURE + LEADERBOARD_FIXTURE
    check("deduplicates repeated sections", len(lb.parse_leaderboard(dup)) == 2)


def test_leaderboard_highlight() -> None:
    import itl_leaderboard as lb
    import itl_app as app

    print("\n[leaderboard highlight]")
    sections = lb.parse_leaderboard(LEADERBOARD_FIXTURE)
    rendered = "\n".join(app.render_section(sections[0]))
    check("highlights the data-you row", "du" in rendered)
    check("marks only one row as you",
          sum(1 for s in sections for r in s.rows if r.is_you) == 1)

    # Fallback name matching, for when the server did not mark anyone.
    unmarked = LEADERBOARD_FIXTURE.replace('data-you="true"', 'data-you="false"')
    sections2 = lb.parse_leaderboard(unmarked)
    check("nobody marked without data-you",
          not any(s.you_row for s in sections2))
    check("mark_you_by_name marks the match",
          lb.mark_you_by_name(sections2, "Max Mustermann") is True)
    check("mark_you_by_name found the right row",
          sections2[0].you_row is not None and sections2[0].you_row.name == "Max Mustermann")
    check("mark_you_by_name ignores unknown names",
          lb.mark_you_by_name(lb.parse_leaderboard(unmarked), "Nobody Here") is False)
    check("mark_you_by_name tolerates None",
          lb.mark_you_by_name(lb.parse_leaderboard(unmarked), None) is False)


def test_all_screens_render() -> None:
    """Every render path must produce output without raising.

    This exists because a bare {RESET} (instead of {ui.RESET}) in an f-string is a
    NameError that only fires when that branch runs -- a whole screen can be broken
    while the suite still passes.
    """
    import itl_app as app
    import itl_client as cm
    import itl_leaderboard as lb

    print("\n[screens render]")

    jwt = fake_jwt()
    client = cm.ItlClient(cm.Session(jwt=jwt, source="test"))
    me = {
        "user": {"id": "u1", "displayName": "Max Mustermann", "provider": "c2",
                 "trust": "verified", "email": None},
        "progress": {"levels": [
            {"level": 0, "title": "How to play", "done": True, "unlocked": True,
             "next": False, "completions": 2, "attemptsLeft": 3, "bestMs": 75298},
            {"level": 1, "title": "Autonomous agent", "done": False, "unlocked": True,
             "next": True, "completions": 0, "attemptsLeft": 5, "bestMs": None},
        ], "totalLevels": 7, "completedCount": 1, "nextLevel": 1,
           "codesLeft": 3, "totalCodes": 10},
    }

    import io
    import contextlib

    def render(label, fn, *args, **kwargs):
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fn(*args, **kwargs)
            check(f"{label} renders", True)
        except Exception as exc:
            check(f"{label} renders", False, f"{type(exc).__name__}: {exc}")

    render("show_identity", app.show_identity, me, client.session)

    sections = lb.parse_leaderboard(LEADERBOARD_FIXTURE)
    render("render_section (no limit)", app.render_section, sections[0])
    render("render_section (limit)", app.render_section, sections[0], limit=1)
    for section in sections:
        for row in section.rows:
            row.is_you = True
    render("render_section (highlighted)", app.render_section, sections[0], limit=1)

    # show_result with and without a discrepancy against the sent time.
    render("show_result (matching)", app.show_result,
           {"status": "recorded", "durationMs": 75298, "bestMs": 75298,
            "isBest": True, "level": 0, "attemptsLeft": 2, "nextLevel": 1,
            "completedCount": 1, "totalLevels": 7, "rank": 1, "rankedOutOf": 165},
           sent_ms=75298)
    render("show_result (mismatch)", app.show_result,
           {"status": "recorded", "durationMs": 3201, "bestMs": 3201,
            "isBest": True, "level": 1, "attemptsLeft": 4},
           sent_ms=60000)
    render("show_result (empty)", app.show_result, {})

    # A bare {RESET} in an f-string is a NameError that only fires when that
    # branch runs. itl_ui.py defines RESET as a module constant, so it is only a
    # bug in modules that import it as `ui`.
    import re
    import os as _os
    for path in ("itl_app.py", "itl_client.py", "itl_leaderboard.py", "itl.py",
                 "itl_time.py"):
        full = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), path)
        with open(full) as fh:
            source = fh.read()
        bare = [m.start() for m in re.finditer(r"(?<!ui\.)(?<!\.)\{RESET\}", source)]
        check(f"{path} has no bare {{RESET}}", not bare, f"at offsets {bare}")


def test_menus() -> None:
    print("\n[menu]")
    test_menu([b"\x1b[B", b"\x1b[B", b"\r"], "down,down,enter", "2")
    test_menu([b"\x1b[A", b"\r"], "up,enter (wraps)", "3")
    test_menu([b"\r"], "enter", "0")
    test_menu([b"q"], "q quits", "None")
    test_menu([b"j", b"j", b"\r"], "vim j,j,enter", "2")
    test_menu([b"3"], "digit shortcut", "2")


def main() -> int:
    port = 8791
    start_mock(port)
    time.sleep(0.3)
    point_at(port)

    test_time_parsing()
    test_header_order()
    test_token_health()
    test_me_shape()
    test_requests_byte_exact(port)
    test_timing_accuracy()
    test_session_loading()
    test_leaderboard_parsing()
    test_leaderboard_highlight()
    test_all_screens_render()
    test_menus()

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

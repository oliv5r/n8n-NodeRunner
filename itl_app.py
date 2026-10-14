"""Interactive CLI: identity panel, level picker, timed run submission."""

from __future__ import annotations

import json
import time
from typing import Any

import itl_client as client_mod
import itl_leaderboard as lb
import itl_time as tmod
import itl_ui as ui

DEFAULT_TOTAL_LEVELS = 7  # from the capture: "totalLevels":7
DEFAULT_DURATION = "01:15:298"


# --------------------------------------------------------------- identity ----

def split_me(me: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split the /api/me response into (user, progress).

    The endpoint answers {"user": {...}, "progress": {...}}. A flat response is
    tolerated too, so an older shape cannot break the tool.
    """
    if isinstance(me.get("user"), dict):
        return me["user"], me.get("progress") or {}
    return me, me


def level_entries(progress: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-level progress list from /api/me."""
    levels = progress.get("levels")
    return [lvl for lvl in levels if isinstance(lvl, dict)] if isinstance(levels, list) else []


def show_identity(me: dict[str, Any], session: client_mod.Session) -> None:
    user, progress = split_me(me)
    levels = level_entries(progress)

    ui.header("node-runner")
    ui.info("Name", user.get("displayName") or user.get("name") or "(unbekannt)", ui.BOLD)
    ui.info("ID", user.get("id") or session.user_id or "?")
    if user.get("provider"):
        ui.info("Provider", user["provider"])
    if user.get("trust"):
        ui.info("Trust", user["trust"])
    if user.get("email"):
        ui.info("E-Mail", user["email"])

    ui.rule()

    total = progress.get("totalLevels") or len(levels)
    done = progress.get("completedCount")
    if done is not None and total:
        ui.info("Fortschritt", f"{done}/{total} Level abgeschlossen")
    if progress.get("nextLevel") is not None:
        ui.info("naechstes", f"Level {progress['nextLevel']}")
    if progress.get("codesLeft") is not None:
        ui.info("Codes", f"{progress['codesLeft']} von {progress.get('totalCodes')} uebrig")

    # Per-level table, so the unlocked/attempts state is visible at a glance.
    if levels:
        print()
        for lvl in levels:
            number = lvl.get("level")
            marks = []
            if lvl.get("done"):
                marks.append(f"{ui.GREEN}fertig{ui.RESET}")
            elif lvl.get("unlocked"):
                marks.append(f"{ui.DIM}offen{ui.RESET}")
            else:
                marks.append(f"{ui.DIM}gesperrt{ui.RESET}")
            if lvl.get("next"):
                marks.append(f"{ui.CYAN}als naechstes{ui.RESET}")

            best = lvl.get("bestMs")
            best_text = f"  best {tmod.format_duration(int(best))}" if best else ""
            attempts = lvl.get("attemptsLeft")
            attempt_text = f"  {attempts} Versuche" if attempts is not None else ""
            title = lvl.get("title") or ""

            print(f"  {number:>2}  {title:<22}{best_text}{attempt_text}   "
                  + "  ".join(marks))

    ui.rule()
    health = client_mod.token_health(session.jwt)
    color = ui.GREEN if health == "ok" else ui.YELLOW
    ui.info("Session", f"{session.source} -- {health}", color)
    print()


def load_best(me: dict[str, Any]) -> dict[int, int]:
    """Best time per level, from the progress list."""
    _, progress = split_me(me)
    best: dict[int, int] = {}

    for lvl in level_entries(progress):
        number, value = lvl.get("level"), lvl.get("bestMs")
        if number is None or value is None:
            continue
        try:
            best[int(number)] = int(value)
        except (TypeError, ValueError):
            continue

    # Older/flat shape: a plain {level: ms} map.
    flat = progress.get("bestMs")
    if isinstance(flat, dict):
        for key, value in flat.items():
            try:
                best.setdefault(int(key), int(value))
            except (TypeError, ValueError):
                continue
    return best


def level_titles(me: dict[str, Any]) -> dict[int, str]:
    """Level number -> title, for nicer menu labels."""
    _, progress = split_me(me)
    out: dict[int, str] = {}
    for lvl in level_entries(progress):
        number = lvl.get("level")
        if number is not None and lvl.get("title"):
            out[int(number)] = lvl["title"]
    return out


# --------------------------------------------------------------- run flow ----

def app_default_duration() -> int:
    return tmod.parse_duration(DEFAULT_DURATION)


def ask_duration() -> int:
    """Prompt until the user enters a parseable mm:ss:ms duration."""
    while True:
        raw = ui.prompt("Zeit (mm:ss:ms)", default=DEFAULT_DURATION)
        try:
            duration = tmod.parse_duration(raw)
        except tmod.TimeFormatError as exc:
            ui.error(str(exc))
            continue
        ui.info("= ", f"{duration} ms")
        return duration


def run_once(
    client: client_mod.ItlClient,
    level: int,
    duration_ms: int,
    *,
    assume_yes: bool = False,
) -> dict[str, Any] | None:
    """Start a run, wait exactly `duration_ms`, then submit it."""
    print()
    try:
        started = ui.spinner(
            f"POST /api/run/start  (level {level})",
            lambda: client.start(level),
        )
    except client_mod.ItlError as exc:
        ui.error(str(exc))
        if exc.status == 401:
            ui.warn("Session abgelaufen oder ungueltig -- mit --url '<launch-url>' neu starten")
        return None

    run_token = started.get("runToken")
    if not run_token:
        ui.error(f"keine runToken in der Antwort: {json.dumps(started)[:300]}")
        return None

    ui.ok(f"runToken erhalten  {ui.DIM}{run_token[:24]}...{ui.RESET}")
    for key in ("serverStartedAt", "expiresInMs", "attemptsLeft"):
        if started.get(key) is not None:
            ui.info(key, started[key])

    # Anchor the target instant on the server's own t0 out of the runToken
    # payload, so a skewed local clock cannot shift the attempt.
    #
    # This is the only thing that matters for the recorded time: the server measures
    # t0 -> arrival of /complete itself and ignores the clientMs we send (verified:
    # claiming 60000 ms after 3.2 s real time recorded durationMs=3201).
    # clientMs is sent anyway because the client sends it, but it is not what the
    # board scores.
    payload = client_mod.jwt_payload(run_token) or {}
    t0 = payload.get("t0")
    now_epoch_ms = time.time() * 1000
    now_perf = time.perf_counter()

    if isinstance(t0, (int, float)):
        target_epoch_ms = t0 + duration_ms
        wait_s = (target_epoch_ms - now_epoch_ms) / 1000.0
        anchor = f"server t0 = {time.strftime('%H:%M:%S', time.gmtime(t0 / 1000))} UTC"
    else:
        wait_s = duration_ms / 1000.0
        anchor = "lokale Uhr (kein t0 im runToken)"

    print()
    ui.info("Zielzeit", tmod.format_duration(duration_ms), ui.BOLD)
    ui.info("clientMs", duration_ms)
    ui.info("Anker", anchor)
    ui.info("Wartezeit", f"{wait_s:.3f} s")
    print(f"  {ui.DIM}Die gewertete Zeit misst der Server selbst von t0 bis zum{ui.RESET}")
    print(f"  {ui.DIM}Eintreffen von /complete -- clientMs wird ignoriert.{ui.RESET}")

    if wait_s < 0:
        ui.error(f"Ziel liegt {abs(wait_s):.3f} s in der Vergangenheit -- Abbruch")
        return None

    if not assume_yes and not ui.confirm(
        f"Jetzt starten und nach {tmod.format_duration(duration_ms)} stoppen?"
    ):
        ui.warn("abgebrochen -- runToken wird verworfen")
        return None

    deadline_perf = now_perf + wait_s

    # Coarse countdown, so the operator can see the clock running.
    remaining_s = wait_s
    while remaining_s > 1.0:
        print(f"\r  {ui.CYAN}t-{remaining_s:>6.1f}s{ui.RESET}", end="", flush=True)
        nap = min(0.1, deadline_perf - time.perf_counter() - 0.001)
        if nap > 0:
            time.sleep(nap)
        remaining_s = deadline_perf - time.perf_counter()
    print("\r" + " " * 14 + "\r", end="")

    actual_perf = tmod.wait_until(deadline_perf)
    drift_ms = (actual_perf - deadline_perf) * 1000.0
    print(f"  {ui.CYAN}->{ui.RESET} sende complete  {ui.DIM}(Sendeabweichung {drift_ms:+.2f} ms){ui.RESET}")

    try:
        return client.complete(run_token, duration_ms, level)
    except client_mod.ItlError as exc:
        ui.error(str(exc))
        return None


def show_result(result: dict[str, Any], sent_ms: int | None = None) -> None:
    print()
    ui.header("Ergebnis")

    if result.get("status"):
        ui.ok(f"status = {result['status']}")

    recorded = result.get("durationMs")
    if recorded is not None:
        ui.info("Dauer", tmod.format_duration(int(recorded)))
        # The server times the run itself and ignores our clientMs, so the two can
        # differ by the request/response round trip plus any network jitter.
        if sent_ms is not None and abs(int(recorded) - sent_ms) > 50:
            delta = int(recorded) - sent_ms
            ui.info("  vs gesendet", f"{tmod.format_duration(sent_ms)} "
                                   f"({delta:+d} ms)", ui.YELLOW)

    if result.get("bestMs") is not None:
        ui.info("Bestzeit", tmod.format_duration(int(result["bestMs"])))
    if result.get("isBest") is not None:
        ui.info("neue Bestzeit", "ja" if result["isBest"] else "nein",
                ui.GREEN if result["isBest"] else "")

    for key in ("level", "completions", "attemptsLeft", "nextLevel",
                "completedCount", "totalLevels", "rank", "rankedOutOf"):
        if result.get(key) is not None:
            ui.info(key, result[key])

    known = {"status", "durationMs", "bestMs", "isBest", "level", "completions",
             "attemptsLeft", "nextLevel", "completedCount", "totalLevels", "rank",
             "rankedOutOf"}
    extra = {k: v for k, v in result.items() if k not in known}
    if extra:
        ui.info("weitere", json.dumps(extra, ensure_ascii=False)[:200])
    print()


# ------------------------------------------------------------ leaderboard ----

def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def render_section(section: lb.Section, *, limit: int | None = None) -> list[str]:
    """Render one board section as lines, with the signed-in row highlighted."""
    head = section.title
    if section.subtitle:
        head += f"  {ui.DIM}{section.subtitle}{ui.RESET}"

    lines = [f"{ui.BOLD}{head}{ui.RESET}"]
    if section.note:
        lines.append(f"  {ui.DIM}{section.note}{ui.RESET}")

    if not section.rows:
        lines.append(f"  {ui.DIM}(noch keine Eintraege){ui.RESET}")
        return lines

    # Name column width, so times line up; clipped so long names cannot break it.
    width = min(max((len(r.name) for r in section.rows), default=8), 24)

    shown = section.rows if limit is None else section.rows[:limit]
    for row in shown:
        name = _clip(row.name, width).ljust(width)
        rank = f"{row.rank:>2}."
        body = f"   {rank}  {name}  {row.time_text:>9}"
        if row.is_you:
            lines.append(f"{ui.GREEN}{ui.BOLD}{body}{ui.RESET}  {ui.GREEN}<- du{ui.RESET}")
        else:
            lines.append(body)

    hidden = len(section.rows) - len(shown)
    if hidden > 0:
        lines.append(f"   {ui.DIM}... {hidden} weitere{ui.RESET}")

    # The user's own rank matters most when they did not make the visible cut.
    you = section.you_row
    if you is not None and limit is not None and you not in shown:
        lines.append(
            f"   {ui.GREEN}{ui.BOLD}{you.rank:>2}.  "
            f"{_clip(you.name, width).ljust(width)}  {you.time_text:>9}{ui.RESET}"
            f"  {ui.GREEN}<- du{ui.RESET}"
        )
    return lines


def leaderboard_screen(client: client_mod.ItlClient, display_name: str | None) -> None:
    """Fetch /leaderboard and browse it section by section."""
    ui.clear()
    try:
        html = ui.spinner("GET /leaderboard", client.leaderboard_html)
    except client_mod.ItlError as exc:
        ui.error(str(exc))
        ui.prompt("Enter zum Fortfahren", default="")
        return

    sections = lb.parse_leaderboard(html)
    if not sections:
        ui.error("keine Rangliste im HTML gefunden (Markup geaendert?)")
        ui.prompt("Enter zum Fortfahren", default="")
        return

    # If the server did not mark anyone (session not recognised), fall back to
    # matching the signed-in display name ourselves.
    if not any(s.you_row for s in sections):
        if lb.mark_you_by_name(sections, display_name):
            ui.warn("Server hat keine eigene Zeile markiert -- per Name zugeordnet")
        elif display_name:
            ui.warn(f"Kein Eintrag fuer {display_name!r} in der Rangliste")

    while True:
        ui.clear()
        ui.header("Leaderboard")

        options = [s.title + (f"  {ui.DIM}{s.subtitle}{ui.RESET}" if s.subtitle else "")
                   for s in sections]
        chosen = ui.choose("Board waehlen:", options + ["Alles anzeigen", "Zurueck"])
        if chosen is None:
            return

        if chosen == len(sections):          # "Alles anzeigen"
            lines: list[str] = []
            for section in sections:
                lines += render_section(section)
                lines.append("")
            ui.pager(lines, title="Leaderboard")
        elif chosen == len(sections) + 1:    # "Zurueck"
            return
        else:
            section = sections[chosen]
            lines = render_section(section)
            you = section.you_row
            if you is not None:
                lines.append("")
                lines.append(f"  {ui.GREEN}Du bist auf Platz {you.rank} "
                             f"({you.time_text}).{ui.RESET}")
            ui.pager(lines, title=f"Leaderboard -- {section.title}")


# ------------------------------------------------------------------- menus ---

def play_screen(client: client_mod.ItlClient, level: int) -> bool:
    """Level panel. Returns True when a run was submitted."""
    ui.clear()
    ui.header(f"Level {level}")
    print(f"  {ui.DIM}Start sendet POST /api/run/start, wartet die Zeit ab und sendet{ui.RESET}")
    print(f"  {ui.DIM}dann POST /api/run/complete mit exakt dieser clientMs.{ui.RESET}")
    print()

    # "Start" jumps straight to the default time; the second entry lets the
    # operator type one. Either way the run itself is confirmed in run_once().
    action = ui.choose("Aktion:", ["Start", "Zeit eingeben und Start", "Zurueck"])
    if action is None or action == 2:
        return False

    duration_ms = app_default_duration() if action == 0 else ask_duration()

    result = run_once(client, level, duration_ms)
    if result is None:
        return False

    show_result(result, sent_ms=duration_ms)
    return True


def level_screen(
    client: client_mod.ItlClient,
    total: int,
    best: dict[int, int],
    display_name: str | None = None,
    titles: dict[int, str] | None = None,
) -> None:
    titles = titles or {}
    while True:
        ui.clear()
        ui.header("Levelauswahl")

        options = []
        for lvl in range(total):
            title = titles.get(lvl, "")
            label = f"Level {lvl}"
            if title:
                label += f"  {ui.DIM}{title}{ui.RESET}"
            if lvl in best:
                label += f"   {ui.GREEN}best {tmod.format_duration(best[lvl])}{ui.RESET}"
            options.append(label)

        leaderboard_index = len(options)
        options.append(f"{ui.CYAN}Leaderboard{ui.RESET}")

        chosen = ui.choose("Level waehlen:", options)
        if chosen is None:
            return
        if chosen == leaderboard_index:
            leaderboard_screen(client, display_name)
            continue
        if play_screen(client, chosen):
            return

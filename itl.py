#!/usr/bin/env python3
"""itl -- CLI client for node-runner-gilt.vercel.app.

Opens, shows your identity from /api/me, lets you pick a level, then starts a
run and submits it after exactly the time you enter (mm:ss:ms).

Usage:
    python3 itl.py                          # use the stored session or $ITL_SESSION
    python3 itl.py --url 'https://node-runner-gilt.vercel.app/play/0?s=<jwt>'
    python3 itl.py --level 2 --time 01:15:298 --yes
    python3 itl.py --check                  # verify session + identity, then exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import itl_app as app
import itl_client as client_mod
import itl_config as cfg
import itl_time as tmod
import itl_ui as ui

DEFAULT_PROFILE = os.path.expanduser("~/.itl_session.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="itl",
        description="node-runner CLI: identity, level picker, timed stop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--jwt", dest="jwt_token", help="session token (otherwise stored profile or $ITL_SESSION)")
    parser.add_argument("--url", help="launch URL containing ?s=<session-token>")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="where the session is stored")
    parser.add_argument("--level", type=int, help="skip the level menu")
    parser.add_argument("--time", dest="time_str", help="skip the time prompt (mm:ss:ms)")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--check", action="store_true", help="print identity, then exit")
    parser.add_argument("--leaderboard", action="store_true", help="open the leaderboard, then exit")
    parser.add_argument("--dry-run", action="store_true", help="build and print requests without sending")
    parser.add_argument("--verbose", "-v", action="store_true", help="dump every request and response")
    return parser


def resolve_session(args: argparse.Namespace) -> client_mod.Session:
    session = client_mod.load_session(args.profile, args.jwt_token or args.url)

    health = client_mod.token_health(session.jwt)
    if "ABGELAUFEN" in health:
        ui.warn(f"Session-Token: {health}")
        ui.warn("Neuen Token holen und mit --jwt '<token>' uebergeben, oder --url '<launch-url>'.")
    return session


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        session = resolve_session(args)
    except client_mod.ItlError as exc:
        ui.error(str(exc))
        return 2

    client = client_mod.ItlClient(session, verbose=args.verbose)

    if args.dry_run:
        ui.warn("--dry-run: es werden keine Anfragen gesendet")
        client.verbose = True
        body = json.dumps({"level": args.level or 0}, separators=(",", ":")).encode()
        for name, value in client._headers(args.level or 0, body):
            print(f"{name}: {value}")
        print(f"\n{body.decode()}")
        return 0

    ui.clear()
    try:
        me = ui.spinner("GET /api/me", client.me)
    except client_mod.ItlError as exc:
        ui.error(str(exc))
        if exc.status in (401, 403):
            print()
            ui.warn("Die Session wurde abgelehnt. Erwartet wird ein frischer Token aus")
            ui.warn("der /play/<n>?s=...-URL (sessionStorage['itl.session']).")
        return 1

    app.show_identity(me, session)

    if args.check:
        return 0

    user, progress = app.split_me(me)

    if args.leaderboard:
        app.leaderboard_screen(client, user.get("displayName"))
        return 0

    # Persist an explicitly supplied session so the next launch needs no arguments.
    if session.source in ("url", "--jwt"):
        try:
            with open(args.profile, "w") as fh:
                json.dump(
                    {"jwt": session.jwt, "referer_level": session.referer_level},
                    fh,
                    indent=2,
                )
            os.chmod(args.profile, 0o600)
            ui.info("gespeichert", args.profile)
        except OSError as exc:
            ui.warn(f"Session konnte nicht gespeichert werden: {exc}")

    best = app.load_best(me)
    total = progress.get("totalLevels") or app.DEFAULT_TOTAL_LEVELS

    # Non-interactive path: level and time given on the command line.
    if args.level is not None:
        if args.level < 0 or args.level >= int(total):
            ui.error(f"Level {args.level} liegt ausserhalb von 0..{int(total) - 1}")
            return 2
        duration = tmod.parse_duration(args.time_str) if args.time_str else app.ask_duration()
        result = app.run_once(client, args.level, duration, assume_yes=args.yes)
        if result is None:
            return 1
        app.show_result(result, sent_ms=duration)
        return 0

    user, _ = app.split_me(me)
    try:
        app.level_screen(client, int(total), best, user.get("displayName"),
                         app.level_titles(me))
    except KeyboardInterrupt:
        print()
        ui.warn("abgebrochen")
        return 130
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        ui.warn("abgebrochen")
        sys.exit(130)

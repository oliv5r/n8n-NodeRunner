"""Terminal UI: identity panel, arrow-key menus, prompt helpers."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Callable, Sequence

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
INVERT = "\033[7m"


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def clear() -> None:
    if _tty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def rule(char: str = "-") -> None:
    width = shutil.get_terminal_size((72, 24)).columns
    print(DIM + char * min(width, 72) + RESET)


def header(title: str) -> None:
    rule("=")
    print(f"{BOLD}{title}{RESET}")
    rule("=")


def info(label: str, value: object, color: str = "") -> None:
    print(f"  {DIM}{label:<14}{RESET}{color}{value}{RESET}")


def ok(msg: str) -> None:
    print(f"{GREEN}  ok{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}warn{RESET}  {msg}")


def error(msg: str) -> None:
    print(f"{RED}err {RESET}  {msg}")


# --------------------------------------------------------------- key reading --

_ARROWS = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",   # normal cursor keys
    "OA": "up", "OB": "down", "OC": "right", "OD": "left",   # application mode
}


def _read_key() -> str:
    """Return 'up'/'down'/'enter'/'q'/... for a single keypress."""
    import select
    import termios
    import tty

    # Read from the fd, not sys.stdin: the TextIOWrapper buffers ahead, which
    # would swallow the rest of an escape sequence and make select() see nothing.
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode(errors="replace")
        if ch == "\x1b":
            # Arrows arrive as ESC [ A..D (or ESC O A..D). A bare ESC has
            # nothing following, so wait only briefly instead of blocking.
            seq = ""
            if select.select([fd], [], [], 0.05)[0]:
                seq = os.read(fd, 2).decode(errors="replace")
            return _ARROWS.get(seq, "escape")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x04":
            return "q"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def choose(
    prompt: str,
    options: Sequence[str],
    *,
    initial: int = 0,
    allow_quit: bool = True,
) -> int | None:
    """Arrow-key menu. Returns the chosen index, or None if the user quit.

    Falls back to a numbered prompt when stdin is not a terminal (pipes, CI).
    """
    if not options:
        raise ValueError("no options")

    if not _tty():
        print(prompt)
        for i, opt in enumerate(options, 1):
            print(f"  {i}) {opt}")
        while True:
            raw = input("number> ").strip()
            if allow_quit and raw.lower() in ("q", "quit", ""):
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            error("invalid selection")

    cursor = max(0, min(initial, len(options) - 1))
    lines = len(options)
    while True:
        print(prompt)
        for i, opt in enumerate(options):
            if i == cursor:
                print(f"  {INVERT}{BOLD}> {opt}{RESET}")
            else:
                print(f"    {opt}")
        if allow_quit:
            print(f"  {DIM}(arrows / enter, q to quit){RESET}")

        key = _read_key()
        if key in ("up", "k"):
            cursor = (cursor - 1) % lines
        elif key in ("down", "j"):
            cursor = (cursor + 1) % lines
        elif key == "enter":
            return cursor
        elif key == "q" and allow_quit:
            return None
        elif key.isdigit() and 1 <= int(key) <= lines:
            return int(key) - 1  # jump straight to an entry

        # Redraw: move back up over the block we just printed.
        sys.stdout.write(f"\033[{lines + (2 if allow_quit else 1)}A")
        sys.stdout.flush()


# ------------------------------------------------------------------- prompts --

def prompt(text: str, default: str = "") -> str:
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    try:
        raw = input(f"{text}{suffix}: ").strip()
    except EOFError:
        return default
    return raw or default


def confirm(text: str, default: bool = True) -> bool:
    marker = "Y/n" if default else "y/N"
    try:
        raw = input(f"{text} [{marker}]: ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes", "j", "ja")


def pager(lines: list[str], *, page_size: int | None = None, title: str = "") -> None:
    """Show `lines` a page at a time. Arrow keys / enter to advance, q to leave."""
    if not lines:
        return
    if page_size is None:
        page_size = max(5, shutil.get_terminal_size((72, 24)).lines - 8)

    if not _tty():
        if title:
            header(title)
        for line in lines:
            print(line)
        return

    total_pages = max(1, (len(lines) + page_size - 1) // page_size)
    page = 0
    while True:
        clear()
        if title:
            header(title)
        start = page * page_size
        for line in lines[start:start + page_size]:
            print(line)
        print()
        if total_pages > 1:
            print(f"  {DIM}Seite {page + 1}/{total_pages}  "
                  f"(links/rechts blaettern, q schliessen){RESET}")
        else:
            print(f"  {DIM}(q schliessen){RESET}")

        key = _read_key()
        if key in ("q", "escape", "enter"):
            return
        if key in ("right", "down", "j", " ") and page < total_pages - 1:
            page += 1
        elif key in ("left", "up", "k") and page > 0:
            page -= 1


def spinner(label: str, fn: Callable[[], object]) -> object:
    """Run fn() behind a lightweight status line."""
    frames = "|/-\\"
    stop = False
    result: list[object] = []
    err: list[BaseException] = []

    if not _tty():
        print(f"{label} ...")
        return fn()

    import threading
    import time

    def spin() -> None:
        i = 0
        while not stop:
            sys.stdout.write(f"\r{DIM}{frames[i % 4]}{RESET} {label}")
            sys.stdout.flush()
            i += 1
            time.sleep(0.08)

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        result.append(fn())
    except BaseException as exc:  # re-raised on the main thread below
        err.append(exc)
    finally:
        stop = True
        thread.join()
        sys.stdout.write("\r" + " " * (len(label) + 4) + "\r")
        sys.stdout.flush()

    if err:
        raise err[0]
    return result[0]

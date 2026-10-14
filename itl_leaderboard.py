"""Parse the server-rendered /leaderboard HTML.

The page is ~213 KB of Next.js SSR output. There is no JSON API (/api/leaderboard
is 404), so the board is scraped out of the rendered DOM. The markup is regular:

    <section class="board-section">
      <h2 class="board-title">Level 1<span class="board-title-sub">...</span></h2>
      <p class="board-note">...</p>
      <ol class="board">
        <li class="board-row" data-you="false">
          <span class="board-rank">1</span>
          <span class="board-who"><span class="board-name">Erika M.</span></span>
          <span class="board-time">4:58.520</span>
        </li>

`data-you="true"` marks the signed-in player's own row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# class names we care about, mapped to the field they fill
_CLASS_FIELDS = {
    "board-section": "section",
    "board-title": "title",
    "board-title-sub": "subtitle",
    "board-note": "note",
    "board-row": "row",
    "board-rank": "rank",
    "board-name": "name",
    "board-time": "time",
}


@dataclass
class Row:
    rank: int
    name: str
    time_text: str
    is_you: bool = False

    @property
    def time_ms(self) -> int | None:
        return parse_board_time(self.time_text)


@dataclass
class Section:
    title: str
    subtitle: str = ""
    note: str = ""
    rows: list[Row] = field(default_factory=list)

    @property
    def you_row(self) -> Row | None:
        return next((r for r in self.rows if r.is_you), None)


def parse_board_time(text: str) -> int | None:
    """Leaderboard times look like '4:58.520' -> minutes:seconds.milliseconds."""
    match = re.match(r"^\s*(\d+):(\d{1,2})[.:](\d{1,3})\s*$", text or "")
    if not match:
        return None
    minutes, seconds, millis = match.groups()
    return (int(minutes) * 60 + int(seconds)) * 1000 + int(millis.ljust(3, "0"))


class _BoardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = []
        self._section: Section | None = None
        self._row: Row | None = None
        # Stack of (field, depth_of_the_element_that_opened_it)
        self._stack: list[tuple[str, int]] = []
        self._buf: list[str] = []
        self._depth = 0

    # -- helpers
    def _current_field(self) -> str | None:
        return self._stack[-1][0] if self._stack else None

    def _flush_text(self) -> None:
        text = "".join(self._buf).strip()
        self._buf = []
        if not text or not self._stack:
            return
        field_name = self._stack[-1][0]
        if field_name == "title" and self._section and not self._section.title:
            self._section.title = text
        elif field_name == "subtitle" and self._section:
            self._section.subtitle = text
        elif field_name == "note" and self._section:
            self._section.note = text
        elif field_name == "rank" and self._row:
            digits = re.sub(r"\D", "", text)
            self._row.rank = int(digits) if digits else 0
        elif field_name == "name" and self._row:
            self._row.name = text
        elif field_name == "time" and self._row:
            self._row.time_text = text

    # -- parser callbacks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        field_name = next((_CLASS_FIELDS[c] for c in classes if c in _CLASS_FIELDS), None)
        if field_name is None:
            return

        self._flush_text()

        if field_name == "section":
            self._section = Section(title="")
            self.sections.append(self._section)
        elif field_name == "row":
            if self._section is None:  # rows before any section header
                self._section = Section(title="")
                self.sections.append(self._section)
            self._row = Row(rank=0, name="", time_text="",
                            is_you=(attr.get("data-you") == "true"))
            self._section.rows.append(self._row)

        self._stack.append((field_name, self._depth))

    def handle_endtag(self, tag: str) -> None:
        self._flush_text()
        while self._stack and self._stack[-1][1] >= self._depth:
            self._stack.pop()
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._stack and data.strip():
            self._buf.append(data)

    def handle_startendtag(self, tag, attrs) -> None:
        pass


def parse_leaderboard(html: str) -> list[Section]:
    """Extract every board section, in page order."""
    parser = _BoardParser()
    parser.feed(html)
    parser._flush_text()

    # Drop empty scaffolding and duplicate sections (the RSC payload repeats the
    # rendered markup, so the same title can show up more than once).
    seen: set[tuple[str, tuple[str, ...]]] = set()
    sections: list[Section] = []
    for section in parser.sections:
        if not section.title and not section.rows:
            continue
        key = (section.title, tuple(r.name for r in section.rows))
        if key in seen:
            continue
        seen.add(key)
        sections.append(section)
    return sections


def mark_you_by_name(sections: list[Section], display_name: str | None) -> bool:
    """Fallback highlight: match the row against the signed-in display name.

    Used when the server did not set data-you (e.g. the board was fetched without
    a valid session). Returns True if anything was marked.
    """
    if not display_name:
        return False
    target = display_name.strip().casefold()
    marked = False
    for section in sections:
        for row in section.rows:
            if not row.is_you and row.name.strip().casefold() == target:
                row.is_you = True
                marked = True
    return marked

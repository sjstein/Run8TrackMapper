#!/usr/bin/env python3
"""Read/write area-label ([area.*]) blocks in a single INI file.

This backs the live authoring server (serve.py). It deliberately does NOT use
configparser for writing: configparser drops comments and reorders/reformats a
file when it rewrites it. Instead we splice only the exact [area.<id>] block a
mutation targets, leaving every other line (comments, spacing, ordering) intact.

Reading is done with config_parser.parse_areas_file (which does use configparser)
so the syntax accepted here is identical to what the rest of the app parses.

A single dict shape is used throughout, matching output_generator.area_to_dict:
    {id, label, tile_x, tile_z, local_x, local_z,
     color?, font_size?, box?, rotation?}
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from config_parser import parse_areas_file

# A section header line: [area.<id>] or any other [section]
_AREA_HEADER_RE = re.compile(r'^\s*\[area\.([^\]]+)\]\s*$')
_ANY_HEADER_RE = re.compile(r'^\s*\[[^\]]+\]\s*$')
# A recognized area key assignment (used to find where a block's data ends, so
# trailing blank lines / comments after the block are left with the file).
_AREA_KEY_RE = re.compile(r'^\s*(label|tile|local|color|font_size|box|rotation|type|end_tile|end_local|repeat)\s*=', re.I)

# Input hardening (#56): the write path is reachable over an authenticated but
# internet-exposed API, so a crafted field must never break out of its [area.<id>]
# block (e.g. a newline in a value, or a `]`/newline in the id, injecting a new
# section or key). These are the last line of defence, enforced at the write layer
# regardless of caller.
MAX_LABEL_LEN = 200
MAX_FIELD_LEN = 120
_ID_RE = re.compile(r'^[A-Za-z0-9_.\-]+$')   # no `[`, `]`, whitespace or newlines


def _safe_field(value, name: str, maxlen: int) -> str:
    """Reject a value that could corrupt the INI (newlines) or is absurdly long."""
    s = str(value)
    if '\n' in s or '\r' in s:
        raise AreaStoreError(f"{name} must not contain newlines")
    if len(s) > maxlen:
        raise AreaStoreError(f"{name} is too long (max {maxlen} characters)")
    return s


def slugify(text: str) -> str:
    """Turn a label into a safe [area.<id>] slug (lowercase, underscores)."""
    slug = re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')
    return slug


def _encode_label(label: str) -> str:
    """Encode a (possibly multi-line) label for a single INI value line: real
    newlines become the literal escape ``\\n`` so the value stays on one line and
    round-trips through configparser and the block-splicer. Decoded on read by
    config_parser._parse_area_sections."""
    return (label or '').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n')


def _fmt_num(value) -> str:
    """Format a coordinate for INI output: integers stay integer, floats keep
    up to 3 significant decimals (trailing zeros trimmed)."""
    f = float(value)
    if f == int(f):
        return str(int(f))
    return f"{round(f, 3):g}"


def format_area_block(area: Dict) -> str:
    """Render one area dict as an [area.<id>] INI block (trailing newline).

    Validates every field that reaches the file so a hostile value cannot inject a
    new section/key (#56): the id must match `_ID_RE` (no `]`/newline/space), the
    label is length-capped (newlines are escaped by `_encode_label`), and free-text
    values (color/type) are newline-checked and length-capped.
    """
    aid = str(area['id'])
    if not _ID_RE.match(aid):
        raise AreaStoreError(f"invalid area id {aid!r} (allowed: letters, digits, _ . -)")
    label = str(area['label'])
    if len(label) > MAX_LABEL_LEN:
        raise AreaStoreError(f"label is too long (max {MAX_LABEL_LEN} characters)")
    lines = [f"[area.{aid}]",
             f"label = {_encode_label(label)}",
             f"tile = {_fmt_num(area['tile_x'])},{_fmt_num(area['tile_z'])}",
             f"local = {_fmt_num(area['local_x'])},{_fmt_num(area['local_z'])}"]
    if area.get('color'):
        lines.append(f"color = {_safe_field(area['color'], 'color', MAX_FIELD_LEN)}")
    if area.get('font_size'):
        lines.append(f"font_size = {int(area['font_size'])}")
    if area.get('box'):
        lines.append("box = true")
    if area.get('rotation'):
        lines.append(f"rotation = {_fmt_num(area['rotation'])}")
    if area.get('type') and area['type'] != 'other':
        lines.append(f"type = {_safe_field(area['type'], 'type', MAX_FIELD_LEN)}")
    # Repeated labels (#95): write the end point + count only for a real repeat
    # (repeat>1 with an end point). area_store validation upstream keeps them numeric.
    try:
        _rep = int(area.get('repeat', 1) or 1)
    except (TypeError, ValueError):
        _rep = 1
    if _rep > 1 and area.get('end_tile_x') is not None and area.get('end_local_x') is not None:
        lines.append(f"end_tile = {_fmt_num(area['end_tile_x'])},{_fmt_num(area['end_tile_z'])}")
        lines.append(f"end_local = {_fmt_num(area['end_local_x'])},{_fmt_num(area['end_local_z'])}")
        lines.append(f"repeat = {_rep}")
    return "\n".join(lines) + "\n"


class AreaStoreError(Exception):
    """Raised for add/update/delete problems (missing id, duplicate id, etc.)."""
    pass


class AreaStore:
    """Add/edit/delete [area.*] blocks in one INI file, preserving the rest."""

    def __init__(self, path):
        self.path = Path(path)

    # ---- reading -------------------------------------------------------
    def read(self) -> List[Dict]:
        """Return the file's area labels as dicts (empty if file is absent)."""
        if not self.path.exists():
            return []
        return [_area_to_dict(a) for a in parse_areas_file(self.path)]

    def ids(self) -> List[str]:
        return [a['id'] for a in self.read()]

    def _read_lines(self) -> List[str]:
        if not self.path.exists():
            return []
        with open(self.path, 'r', encoding='utf-8') as f:
            return f.read().splitlines()

    def _find_block(self, lines: List[str], area_id: str) -> Optional[tuple]:
        """Return (start, end) line indices [start, end) covering the
        [area.<id>] header through its last non-blank value line, or None."""
        start = None
        for i, ln in enumerate(lines):
            m = _AREA_HEADER_RE.match(ln)
            if m and m.group(1) == area_id:
                start = i
                break
        if start is None:
            return None
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _ANY_HEADER_RE.match(lines[j]):
                end = j
                break
        # Back off past trailing blank lines and comments so they stay in the
        # file: a block owns only its header through its last key = value line.
        while end - 1 > start and not _AREA_KEY_RE.match(lines[end - 1]):
            end -= 1
        return (start, end)

    # ---- writing -------------------------------------------------------
    def add(self, area: Dict) -> Dict:
        """Append a new area block. Raises if the id already exists in this file."""
        area_id = area['id']
        lines = self._read_lines()
        if self._find_block(lines, area_id) is not None:
            raise AreaStoreError(f"Area id '{area_id}' already exists in {self.path.name}")
        block = format_area_block(area).splitlines()
        if lines and lines[-1].strip() != '':
            lines.append('')          # blank separator before the new block
        lines.extend(block)
        self._write_lines(lines)
        return area

    def update(self, area_id: str, area: Dict) -> Dict:
        """Replace the [area.<id>] block in place. Raises if not found here."""
        lines = self._read_lines()
        span = self._find_block(lines, area_id)
        if span is None:
            raise AreaStoreError(
                f"Area id '{area_id}' is not in {self.path.name} "
                f"(it may be defined inline in the config or another areas file)")
        start, end = span
        new_block = format_area_block(area).splitlines()
        lines[start:end] = new_block
        self._write_lines(lines)
        return area

    def delete(self, area_id: str) -> None:
        """Remove the [area.<id>] block. Raises if not found here."""
        lines = self._read_lines()
        span = self._find_block(lines, area_id)
        if span is None:
            raise AreaStoreError(
                f"Area id '{area_id}' is not in {self.path.name}")
        start, end = span
        del lines[start:end]
        # Collapse a doubled blank line left behind by the removal.
        if (start < len(lines) and lines[start].strip() == ''
                and (start == 0 or lines[start - 1].strip() == '')):
            del lines[start]
        self._write_lines(lines)

    def _write_lines(self, lines: List[str]) -> None:
        """Atomically write the file (temp + replace), backing up the previous
        contents to <file>.bak first so a bad edit is always recoverable."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + '.bak'))
        text = "\n".join(lines)
        if text and not text.endswith("\n"):
            text += "\n"
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   prefix=self.path.name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def _area_to_dict(area) -> Dict:
    """AreaLabel -> dict (same shape as output_generator.area_to_dict)."""
    d = {
        "id": area.id,
        "label": area.label,
        "tile_x": area.tile[0],
        "tile_z": area.tile[1],
        "local_x": area.local[0],
        "local_z": area.local[1],
    }
    if area.color:
        d["color"] = area.color
    if area.font_size:
        d["font_size"] = area.font_size
    if area.box:
        d["box"] = True
    if area.rotation:
        d["rotation"] = area.rotation
    if getattr(area, "type", None):
        d["type"] = area.type
    # Repeated labels (#95): end point + count, only when it's a real repeat.
    if getattr(area, "repeat", 1) and area.repeat > 1 and area.end_tile is not None and area.end_local is not None:
        d["end_tile_x"] = area.end_tile[0]
        d["end_tile_z"] = area.end_tile[1]
        d["end_local_x"] = area.end_local[0]
        d["end_local_z"] = area.end_local[1]
        d["repeat"] = area.repeat
    return d

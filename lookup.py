"""'Have I contacted them already?' — understand whatever the user pastes
(channel link, @handle, video/Shorts link, channel ID or a plain name) and
match it against the contacted list, offline first, then via the API."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

CHANNEL_ID_RE = re.compile(r"\bUC[0-9A-Za-z_-]{22}\b")
VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")


def norm_handle(h: str) -> str:
    return unquote(h or "").strip().lstrip("@").lower()


def natural_key(s: str):
    """'Channel2' before 'Channel10' (like Windows Explorer)."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", (s or "").casefold())]


def norm_name(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", (s or "").lower()).strip()


def parse_ref(text: str) -> tuple[str, str]:
    """-> (kind, value), kind in: id, handle, user, custom, video, name, empty."""
    t = (text or "").strip().strip("<>\"' ")
    if not t:
        return "empty", ""
    m = CHANNEL_ID_RE.search(t)
    if m:
        return "id", m.group(0)
    if t.startswith("@") and " " not in t:
        return "handle", norm_handle(t)
    looks_url = "youtube.com" in t.lower() or "youtu.be" in t.lower()
    if looks_url:
        u = urlparse(t if "://" in t else "https://" + t)
        host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
        if host.endswith("youtu.be") and parts and VIDEO_ID_RE.match(parts[0]):
            return "video", parts[0]
        v = parse_qs(u.query).get("v", [""])[0]
        if VIDEO_ID_RE.match(v):
            return "video", v
        if parts:
            if parts[0].startswith("@"):
                return "handle", norm_handle(parts[0])
            if parts[0] in ("shorts", "live", "embed") and len(parts) > 1 and VIDEO_ID_RE.match(parts[1]):
                return "video", parts[1]
            if parts[0] == "channel" and len(parts) > 1:
                return "id", parts[1]
            if parts[0] == "user" and len(parts) > 1:
                return "user", unquote(parts[1])
            if parts[0] == "c" and len(parts) > 1:
                return "custom", unquote(parts[1])
        return "empty", ""
    return "name", t


def find_offline(rows: list[dict], kind: str, value: str) -> list[dict]:
    """Contacted rows matching the reference (exact for ids/handles, fuzzy for names)."""
    if kind == "id":
        return [r for r in rows if r["channel_id"] == value]
    if kind in ("handle", "custom", "user"):
        h = norm_handle(value)
        exact = [r for r in rows if norm_handle(r["handle"]) == h]
        if exact or kind == "handle":
            return exact
        value = h  # /c/Name or /user/Name: fall back to the name
    if kind in ("name", "custom", "user"):
        q = norm_name(value)
        if not q:
            return []
        exact = [r for r in rows if norm_name(r["title"]) == q or norm_handle(r["handle"]) == q.replace(" ", "")]
        return exact or [r for r in rows if q in norm_name(r["title"])]
    return []

"""Outreach tracking: statuses, follow-up reminders, spotting the video a creator
published about your game, and one-click email drafts (Gmail or the default mail app).

Nothing here sends email: it only opens a pre-filled draft that you review and send.
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urlencode

STATUSES = ["Contacted", "Followed up", "Replied", "Key sent", "Video published", "Declined", "No reply"]
WAITING = {"Contacted", "Followed up"}      # we're waiting for an answer → may need a follow-up
STATUS_ICON = {"Contacted": "✉", "Followed up": "↻", "Replied": "↩", "Key sent": "★",
               "Video published": "▶", "Declined": "✕", "No reply": "…"}
DAY = 86400.0
MAIL_APPS = ["Gmail (browser)", "Outlook (browser)", "Default mail app"]


# ---- follow-ups ---------------------------------------------------------------
def days_since(ts: float, now: float | None = None) -> int:
    return max(0, int(((now or time.time()) - (ts or 0)) // DAY))


def needs_followup(row: dict, after_days: int, now: float | None = None) -> bool:
    """True when we're still waiting for an answer and it's been `after_days` days."""
    if after_days <= 0 or row.get("status", "Contacted") not in WAITING:
        return False
    return days_since(row.get("status_ts") or row.get("ts") or 0, now) >= after_days


def followup_count(rows: list[dict], after_days: int, now: float | None = None) -> int:
    return sum(needs_followup(r, after_days, now) for r in rows)


# ---- published videos ---------------------------------------------------------
def uploads_playlists(client, channel_ids: list[str]) -> dict[str, str]:
    """{channel_id: uploads playlist id} — 1 unit per 50 channels. Falls back to the
    UC… → UU… convention if a channel isn't returned."""
    out = {}
    try:
        for item in client.channels(channel_ids):
            pl = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if pl:
                out[item["id"]] = pl
    except Exception:  # noqa: BLE001 — fall back to the naming convention below
        pass
    for cid in channel_ids:
        if cid not in out and cid.startswith("UC"):
            out[cid] = "UU" + cid[2:]
    return out


def _published_ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return None


def find_published(client, rows: list[dict], game: str, appid=None, n_videos: int = 30,
                   progress=None, cancel=None) -> list[tuple[dict, dict]]:
    """For each contacted creator, look at their latest uploads (≈2 units each) for a
    video about `game` published after you contacted them. Returns [(row, video)],
    where video = {url, title, views, found_in}."""
    from scout import match_video  # local import: scout is heavy and not needed for the rest

    found = []
    todo = [r for r in rows if r.get("status") not in ("Declined", "Video published")]
    playlists = uploads_playlists(client, [r["channel_id"] for r in todo]) if todo else {}
    todo = [r for r in todo if playlists.get(r["channel_id"])]
    for i, r in enumerate(todo, 1):
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(f"Checking {r['title']}… ({i}/{len(todo)})")
        vids = client.uploads(playlists[r["channel_id"]], n=n_videos)
        since = (r.get("ts") or 0) - DAY  # a day of slack for time zones
        vids = [v for v in vids if (_published_ts(v.get("published")) or time.time()) >= since]
        if not vids:
            continue
        details = client.video_details([v["video_id"] for v in vids])
        hits = []
        for v in vids:
            d = details.get(v["video_id"], {})
            where = match_video(v["title"], d.get("tags", []), v.get("description", ""), game, appid)
            if where:
                hits.append({"url": f"https://www.youtube.com/watch?v={v['video_id']}", "title": v["title"],
                             "views": d.get("views"), "found_in": where, "published": v.get("published")})
        if hits:
            hits.sort(key=lambda h: h["published"] or "")  # the first video they made about it
            found.append((r, hits[0]))
    return found


# ---- email drafts -------------------------------------------------------------
FOLLOWUP_TEMPLATE = """Subject: Re: {game} — a free key for you

Hi {creator},

Just a quick follow-up on my email about {game}, in case it got buried.
I'd still love to send you a free Steam key — no pressure at all.

Thanks!
[YOUR NAME]
"""


def followup_draft(creator: str, game: str, template: str = FOLLOWUP_TEMPLATE) -> str:
    try:
        return template.format(creator=creator, game=game or "our game")
    except (KeyError, IndexError, ValueError):
        return FOLLOWUP_TEMPLATE.format(creator=creator, game=game or "our game")


SUBJECT_RE = re.compile(r"^\s*subject\s*:\s*(.*)$", re.I)


def split_draft(text: str) -> tuple[str, str]:
    """'Subject: …' on the first non-empty line becomes the subject; the rest is the body."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = SUBJECT_RE.match(line)
        if m:
            body = "\n".join(lines[i + 1:]).strip("\n")
            return m.group(1).strip(), body
        break
    return "", (text or "").strip("\n")


def compose_url(app: str, to: str, subject: str, body: str) -> str:
    """A link that opens a pre-filled draft. Nothing is sent automatically."""
    if app.startswith("Gmail"):
        return "https://mail.google.com/mail/?" + urlencode(
            {"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}, quote_via=quote)
    if app.startswith("Outlook"):
        return "https://outlook.live.com/mail/0/deeplink/compose?" + urlencode(
            {"to": to, "subject": subject, "body": body}, quote_via=quote)
    q = urlencode({"subject": subject, "body": body.replace("\n", "\r\n")}, quote_via=quote)
    return f"mailto:{quote(to, safe='@.+-_')}?{q}"

"""Local storage: API response cache, contacted-creator history, saved settings.

Everything lives in one folder per user:
  Windows: %APPDATA%\\CreatorScout
  other:   ~/.creatorscout
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("APPDATA")
    d = Path(base) / "CreatorScout" if base else Path.home() / ".creatorscout"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "creatorscout.sqlite3")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_cache(
                key TEXT PRIMARY KEY, body TEXT NOT NULL, ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS contacted(
                channel_id TEXT PRIMARY KEY, title TEXT, ts REAL NOT NULL, note TEXT);
            CREATE TABLE IF NOT EXISTS manual_email(
                channel_id TEXT PRIMARY KEY, email TEXT NOT NULL, ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS email_review(
                channel_id TEXT NOT NULL, email TEXT NOT NULL, status TEXT NOT NULL, ts REAL NOT NULL,
                PRIMARY KEY(channel_id, email));
            """
        )
        for col, typ in (("handle", "TEXT"), ("email", "TEXT"), ("url", "TEXT"),  # added in later versions
                         ("status", "TEXT"), ("status_ts", "REAL"), ("video_url", "TEXT"),
                         ("video_title", "TEXT"), ("video_views", "INTEGER")):
            try:
                self._db.execute(f"ALTER TABLE contacted ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        self._db.commit()

    # ---- API cache -------------------------------------------------------
    def cache_get(self, key: str, ttl_seconds: float):
        with self._lock:
            row = self._db.execute(
                "SELECT body, ts FROM api_cache WHERE key=?", (key,)
            ).fetchone()
        if row and time.time() - row[1] < ttl_seconds:
            return json.loads(row[0])
        return None

    def cache_put(self, key: str, body) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO api_cache(key, body, ts) VALUES(?,?,?)",
                (key, json.dumps(body), time.time()),
            )
            self._db.commit()

    def cache_clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM api_cache")
            self._db.commit()

    # ---- contacted history ----------------------------------------------
    def contacted_ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT channel_id FROM contacted")}

    def set_contacted(self, channel_id: str, title: str, value: bool,
                      handle: str = "", email: str = "", url: str = "") -> None:
        with self._lock:
            if value:
                old = self._db.execute("SELECT 1 FROM contacted WHERE channel_id=?", (channel_id,)).fetchone()
                if old:  # already tracked: keep date, status, note and video; fill in new details
                    for col, val in (("title", title), ("handle", handle), ("email", email), ("url", url)):
                        if val:
                            self._db.execute(f"UPDATE contacted SET {col}=? WHERE channel_id=?", (val, channel_id))
                else:
                    now = time.time()
                    self._db.execute(
                        "INSERT INTO contacted(channel_id, title, ts, note, handle, email, url, status, status_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (channel_id, title, now, "", handle or "", email or "", url or "", "Contacted", now),
                    )
            else:
                self._db.execute("DELETE FROM contacted WHERE channel_id=?", (channel_id,))
            self._db.commit()


    def contacted_rows(self) -> list[dict]:
        with self._lock:
            cur = self._db.execute(
                "SELECT channel_id, title, ts, COALESCE(note,''), COALESCE(handle,''), "
                "COALESCE(email,''), COALESCE(url,''), COALESCE(status,'Contacted'), COALESCE(status_ts, ts), "
                "COALESCE(video_url,''), COALESCE(video_title,''), video_views FROM contacted ORDER BY ts DESC")
            keys = ("channel_id", "title", "ts", "note", "handle", "email", "url", "status", "status_ts",
                    "video_url", "video_title", "video_views")
            return [dict(zip(keys, r)) for r in cur]

    def set_status(self, channel_id: str, status: str, ts: float | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE contacted SET status=?, status_ts=? WHERE channel_id=?",
                             (status, ts or time.time(), channel_id))
            self._db.commit()

    def set_video(self, channel_id: str, url: str, title: str, views: int | None) -> None:
        """Remember the video they published about your game (and mark it as published)."""
        with self._lock:
            self._db.execute(
                "UPDATE contacted SET video_url=?, video_title=?, video_views=?, status='Video published', "
                "status_ts=CASE WHEN COALESCE(status,'')='Video published' THEN status_ts ELSE ? END "
                "WHERE channel_id=?", (url, title, views, time.time(), channel_id))
            self._db.commit()

    def contacted_times(self) -> dict[str, float]:
        with self._lock:
            return dict(self._db.execute("SELECT channel_id, ts FROM contacted"))

    def set_contacted_note(self, channel_id: str, note: str) -> None:
        with self._lock:
            self._db.execute("UPDATE contacted SET note=? WHERE channel_id=?", (note, channel_id))
            self._db.commit()

    def update_contacted_info(self, channel_id: str, handle: str = "", title: str = "") -> None:
        """Fill in details learned later (e.g. the @handle) without touching the date."""
        with self._lock:
            if handle:
                self._db.execute("UPDATE contacted SET handle=? WHERE channel_id=? AND COALESCE(handle,'')=''",
                                 (handle, channel_id))
            if title:
                self._db.execute("UPDATE contacted SET title=? WHERE channel_id=?", (title, channel_id))
            self._db.commit()

    # ---- your verdicts on found emails ('ok' / 'bad') ----------------------
    def email_reviews(self) -> dict[tuple[str, str], str]:
        with self._lock:
            return {(c, e): st for c, e, st in self._db.execute("SELECT channel_id, email, status FROM email_review")}

    def set_email_review(self, channel_id: str, email: str, status: str | None) -> None:
        with self._lock:
            if status:
                self._db.execute("INSERT OR REPLACE INTO email_review VALUES(?,?,?,?)",
                                 (channel_id, email, status, time.time()))
            else:
                self._db.execute("DELETE FROM email_review WHERE channel_id=? AND email=?", (channel_id, email))
            self._db.commit()

    # ---- emails added by hand (from the YouTube About tab) -------------
    def manual_emails(self) -> dict[str, str]:
        with self._lock:
            return dict(self._db.execute("SELECT channel_id, email FROM manual_email"))

    def set_manual_email(self, channel_id: str, email: str | None) -> None:
        with self._lock:
            if email:
                self._db.execute(
                    "INSERT OR REPLACE INTO manual_email(channel_id, email, ts) VALUES(?,?,?)",
                    (channel_id, email, time.time()),
                )
            else:
                self._db.execute("DELETE FROM manual_email WHERE channel_id=?", (channel_id,))
            self._db.commit()


# ---- settings (plain JSON so it is easy to inspect/edit) -----------------
def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    p = settings_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def save_settings(d: dict) -> None:
    settings_path().write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

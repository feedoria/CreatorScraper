"""Outreach tracker: statuses, follow-up reminders, DB migration, email drafts, and
spotting the video a contacted creator published about your game."""
import sqlite3, sys, tempfile, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import outreach
from storage import Store
from youtube_api import YouTubeClient
from fake_youtube import World

DAY = 86400

# ---- an old database (before statuses existed) is upgraded in place -----------
db = Path(tempfile.mkdtemp()) / "old.sqlite3"
con = sqlite3.connect(db)
con.executescript("CREATE TABLE contacted(channel_id TEXT PRIMARY KEY, title TEXT, ts REAL NOT NULL, note TEXT);")
con.execute("INSERT INTO contacted VALUES('UCold', 'Old Friend', ?, 'sent key')", (time.time() - 20 * DAY,))
con.commit(); con.close()
store = Store(db)
old = store.contacted_rows()[0]
assert old["status"] == "Contacted" and old["status_ts"] == old["ts"] and old["note"] == "sent key", old
assert old["video_url"] == "" and old["video_views"] is None
print("old database upgraded: every contact starts as 'Contacted' ✓")

# ---- statuses and follow-ups ------------------------------------------------------
assert outreach.needs_followup(old, 7)                       # 20 days, no answer
assert not outreach.needs_followup(old, 0)                   # reminders off
assert not outreach.needs_followup(old, 30)
store.set_status("UCold", "Replied")
r = store.contacted_rows()[0]
assert r["status"] == "Replied" and not outreach.needs_followup(r, 7)
store.set_status("UCold", "Followed up")                     # the clock restarts
assert not outreach.needs_followup(store.contacted_rows()[0], 7)
store.set_status("UCold", "Followed up", ts=time.time() - 8 * DAY)
assert outreach.needs_followup(store.contacted_rows()[0], 7)
assert outreach.followup_count(store.contacted_rows(), 7) == 1
# marking someone as contacted again keeps their history
store.set_contacted("UCold", "Old Friend", True, email="old@friend.tv")
r = store.contacted_rows()[0]
assert r["status"] == "Followed up" and r["note"] == "sent key" and r["email"] == "old@friend.tv", r
print("statuses, follow-up reminders and re-marking keep history ✓")

# ---- email drafts -------------------------------------------------------------
subject, body = outreach.split_draft("\nSubject: Digger's End — free key\n\nHi Mole,\n\nLine two.\n")
assert subject == "Digger's End — free key" and body.startswith("Hi Mole,") and "Subject" not in body
assert outreach.split_draft("Hi there") == ("", "Hi there")
g = urlparse(outreach.compose_url("Gmail (browser)", "a@b.tv", subject, body))
q = parse_qs(g.query)
assert g.netloc == "mail.google.com" and q["to"] == ["a@b.tv"] and q["su"] == [subject] and q["body"] == [body]
o = parse_qs(urlparse(outreach.compose_url("Outlook (browser)", "a@b.tv", subject, body)).query)
assert o["subject"] == [subject] and o["body"] == [body]
m = outreach.compose_url("Default mail app", "a+yt@b.tv", subject, body)
assert m.startswith("mailto:a+yt@b.tv?") and " " not in m
mq = parse_qs(m.split("?", 1)[1])
assert mq["subject"] == [subject] and mq["body"][0] == body.replace("\n", "\r\n")
fu = outreach.followup_draft("Mole", "Digger's End")
assert outreach.split_draft(fu)[0].startswith("Re: Digger's End") and "Hi Mole" in fu
print("drafts: subject split, Gmail / Outlook / mailto links round-trip exactly ✓")

# ---- spotting published videos ------------------------------------------------
GAME, APPID = "Digger's End", 3456780
w = World(seed=3, n=12)
ch = w.ch
def make(cid, j, age, title="Day 1", tags=(), desc="Like and subscribe"):
    v = ch[cid]["vids"][j]; v.update(title=title, tags=list(tags), desc=desc, age=age, views=4321)
make("UC0001", 0, 2, title="Digger's End is the best digging game")                 # after contact: title
make("UC0002", 0, 3, title="I can't stop playing this", tags=["digger's end", "indie"])  # after contact: tags
make("UC0003", 0, 1, desc=f"Get it: https://store.steampowered.com/app/{APPID}/")     # after contact: Steam link
make("UC0004", 0, 40, title="Digger's End first look")                               # BEFORE we contacted them
make("UC0005", 0, 2, title="Digger's End again")                                     # declined → not checked
for cid in ("UC0001", "UC0002", "UC0003", "UC0004", "UC0005", "UC0006"):
    for v in ch[cid]["vids"][1:]:
        v["age"] = max(v["age"], 60)          # nothing else recent
st = Store(Path(tempfile.mkdtemp()) / "s.sqlite3")
for cid in ("UC0001", "UC0002", "UC0003", "UC0004", "UC0005", "UC0006"):
    st.set_contacted(cid, ch[cid]["title"], True)
with st._lock:
    st._db.execute("UPDATE contacted SET ts=?, status_ts=?", (time.time() - 10 * DAY, time.time() - 10 * DAY))
    st._db.commit()
st.set_status("UC0005", "Declined")
client = YouTubeClient("k", st, w.session())
found = outreach.find_published(client, st.contacted_rows(), GAME, APPID)
got = {r["channel_id"]: v for r, v in found}
assert set(got) == {"UC0001", "UC0002", "UC0003"}, set(got)
assert got["UC0001"]["found_in"] == "title" and got["UC0002"]["found_in"] == "tags"
assert got["UC0003"]["found_in"] == "steam link" and got["UC0001"]["views"] == 4321
assert got["UC0001"]["url"] == "https://www.youtube.com/watch?v=UC0001_0"
units = client.units_used
assert units <= 1 + 2 * 5, units                      # 1 for the playlists + ~2 per creator
for r, v in found:
    st.set_video(r["channel_id"], v["url"], v["title"], v["views"])
rows = {r["channel_id"]: r for r in st.contacted_rows()}
assert rows["UC0001"]["status"] == "Video published" and rows["UC0001"]["video_views"] == 4321
assert rows["UC0004"]["status"] == "Contacted"        # old video, before our email
# published creators aren't checked again
again = outreach.find_published(YouTubeClient("k", st, w.session()), st.contacted_rows(), GAME, APPID)
assert not again, again
print(f"published videos: found in title / tags / Steam link, ignored pre-contact & declined, {units} units ✓")
print("OUTREACH TESTS PASSED")

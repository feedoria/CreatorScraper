"""'Have I contacted them?': link parsing, offline matching, API resolution, DB migration."""
import sqlite3, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lookup
from storage import Store
from youtube_api import YouTubeClient

CID = "UC" + "a1B2c3D4e5F6g7H8i9J0kL"   # 24 chars
cases = {
    "https://www.youtube.com/@DeepDigger": ("handle", "deepdigger"),
    "youtube.com/@deep.digger/videos": ("handle", "deep.digger"),
    "@DeepDigger": ("handle", "deepdigger"),
    f"https://www.youtube.com/channel/{CID}": ("id", CID),
    CID: ("id", CID),
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s": ("video", "dQw4w9WgXcQ"),
    "https://youtu.be/dQw4w9WgXcQ?si=abc": ("video", "dQw4w9WgXcQ"),
    "https://www.youtube.com/shorts/dQw4w9WgXcQ": ("video", "dQw4w9WgXcQ"),
    "https://www.youtube.com/user/DiggerOld": ("user", "DiggerOld"),
    "https://www.youtube.com/c/Deep%20Digger": ("custom", "Deep Digger"),
    "Deep Digger": ("name", "Deep Digger"),
    "   ": ("empty", ""),
}
for text, want in cases.items():
    got = lookup.parse_ref(text)
    assert got == want, (text, got, want)
print(f"parse_ref: {len(cases)} link formats recognised ✓")

rows = [
    {"channel_id": CID, "title": "Deep Digger", "handle": "deepdigger", "note": "", "email": "", "url": "", "ts": 0},
    {"channel_id": "UC" + "x" * 22, "title": "Cozy Sims HQ", "handle": "", "note": "", "email": "", "url": "", "ts": 0},
]
assert [r["title"] for r in lookup.find_offline(rows, "handle", "deepdigger")] == ["Deep Digger"]
assert [r["title"] for r in lookup.find_offline(rows, "name", "deep digger")] == ["Deep Digger"]
assert [r["title"] for r in lookup.find_offline(rows, "name", "cozy")] == ["Cozy Sims HQ"]
assert lookup.find_offline(rows, "handle", "deepdig") == []          # handles must match exactly
assert [r["title"] for r in lookup.find_offline(rows, "custom", "DeepDigger")] == ["Deep Digger"]
print("find_offline ✓")

# migration: a database from the previous version (contacted without handle/email/url)
db = Path(tempfile.mkdtemp()) / "old.sqlite3"
con = sqlite3.connect(db)
con.execute("CREATE TABLE contacted(channel_id TEXT PRIMARY KEY, title TEXT, ts REAL NOT NULL, note TEXT)")
con.execute("INSERT INTO contacted VALUES(?,?,?,?)", (CID, "Deep Digger", time.time(), None)); con.commit(); con.close()
st = Store(db)
r = st.contacted_rows()[0]
assert r["handle"] == "" and r["note"] == "", r
st.update_contacted_info(CID, handle="deepdigger"); st.set_contacted_note(CID, "sent key")
st.set_contacted(CID, "Deep Digger", True, handle="deepdigger")       # re-marking keeps the note
assert st.contacted_rows()[0]["note"] == "sent key"
print("old database migrated, notes kept ✓")

# API resolution with a fake YouTube
class R:
    def __init__(s, b): s.status_code, s._b, s.text = 200, b, ""
    def json(s): return s._b
CH = {"id": CID, "snippet": {"title": "Deep Digger", "customUrl": "@deepdigger"}, "statistics": {"subscriberCount": "24000"}}
class Sess:
    def __init__(s): s.calls = []
    def get(s, url, params, timeout):
        ep = url.rsplit("/", 1)[1]; s.calls.append((ep, {k: v for k, v in params.items() if k != "key"}))
        if ep == "channels":
            ok = params.get("forHandle") == "@deepdigger" or params.get("id") == CID or params.get("forUsername") == "DiggerOld"
            return R({"items": [CH] if ok else []})
        if ep == "videos":
            return R({"items": [{"id": "dQw4w9WgXcQ", "snippet": {"channelId": CID}}]})
        if ep == "search":
            assert params["type"] == "channel"
            return R({"items": [{"id": {"channelId": CID}}]})
sess = Sess()
cl = YouTubeClient("K", Store(Path(tempfile.mkdtemp()) / "c.sqlite3"), sess)
for kind, val in [("handle", "deepdigger"), ("video", "dQw4w9WgXcQ"), ("user", "DiggerOld"), ("name", "deep digger")]:
    res = cl.resolve(kind, val)
    assert res and res[0]["channel_id"] == CID and res[0]["handle"] == "deepdigger" and res[0]["subs"] == 24000, (kind, res)
assert cl.resolve("handle", "nobody") == []
print(f"resolve via API: handle, video, user, name ✓  ({cl.units_used} units)")
print("LOOKUP TESTS PASSED")

"""Games found beyond titles (tags, descriptions, Steam links) and beyond the last 30 uploads."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import scout
from fake_youtube import APPIDS, GAMES, World
from storage import Store
from youtube_api import YouTubeClient

m = scout.match_video
assert m("This game is INSANE", ["Hydroneer", "gameplay"], "", "Hydroneer") == "tags"
assert m("New favourite?!", [], "Get it: https://store.steampowered.com/app/100000/", "Hydroneer", 100000) == "steam link"
assert m("Day 1", [], "Today we play Dome Keeper with friends", "Dome Keeper") == "description"
assert m("Day 1", [], "keeper of the dome", "Dome Keeper") is None          # descriptions need the exact name
assert m("Day 1", ["minecraft"], "https://store.steampowered.com/app/100001/", "Hydroneer", 100000) is None
print("match_video: tags, Steam links and exact names in descriptions ✓")

w = World(seed=11, n=100, n_videos=120, rich=True)
truth = {cid: set(c["covered"]) for cid, c in w.ch.items()}

def detected(n_videos, title_only):
    client = YouTubeClient("K", Store(Path(tempfile.mkdtemp()) / "t.sqlite3"), w.session())
    old = scout.match_video
    if title_only:
        scout.match_video = lambda t, tags, d, g, a=None: "title" if scout.mentions(t, g) else None
    found, units = 0, 0
    try:
        for cid in w.ch:
            c = scout.Candidate(channel_id=cid)
            scout.fill_channel(c, client.channels([cid])[0])
            scout.analyse(c, client, GAMES, n_videos=n_videos, appids={} if title_only else APPIDS)
            found += len(c.matched_games & truth[cid])
    finally:
        scout.match_video = old
    return found, client.units_used

total = sum(len(v) for v in truth.values())
old_found, old_units = detected(30, title_only=True)
new_found, new_units = detected(100, title_only=False)
print(f"real coverage in the simulated channels: {total} (channel, game) pairs")
print(f"  before (titles only, last 30 videos):  {old_found} found ({100*old_found/total:.0f}%)  · {old_units} units")
print(f"  now (titles+tags+desc+Steam, last 100): {new_found} found ({100*new_found/total:.0f}%)  · {new_units} units")
assert new_found > 1.8 * old_found, (old_found, new_found)
# no false positives: everything detected is real
client = YouTubeClient("K", Store(Path(tempfile.mkdtemp()) / "t2.sqlite3"), w.session())
fp = 0
for cid in list(w.ch)[:40]:
    c = scout.Candidate(channel_id=cid); scout.fill_channel(c, client.channels([cid])[0])
    scout.analyse(c, client, GAMES, n_videos=120, appids=APPIDS)
    fp += len(c.matched_games - truth[cid])
assert fp == 0, fp
print("no false matches ✓")
print("COVERAGE SOURCES TESTS PASSED")

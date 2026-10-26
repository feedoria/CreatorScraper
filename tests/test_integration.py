"""Whole pipeline on a simulated YouTube with a known truth."""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import scout
from fake_youtube import GAMES, World
from storage import Store
from youtube_api import YouTubeClient

w = World(seed=7)
client = YouTubeClient("KEY", Store(Path(tempfile.mkdtemp()) / "t.sqlite3"), w.session())
st = scout.Settings(game_name="Digger's End", game_description="A digging simulator: dig underground, mine ore and gems, drills, caves, upgrades.",
                    comparable_games=GAMES, keywords=[], min_subs=500, max_subs=5_000_000, max_deep=150)
t = time.time()
ranked, stats = scout.run(st, client)
m = stats["model"]
print(f"ranked {len(ranked)} channels in {time.time()-t:.1f}s · {stats['units']} API units · model learned={m.learned} rows={m.rows}")
print("strongest signals:", m.summary(4))
top = ranked[:15]
love_top = sum(w.ch[c.channel_id]["love"] for c in top) / len(top)
base = sum(w.ch[c.channel_id]["love"] for c in ranked) / len(ranked)
print(f"genre lovers: {love_top:.0%} of the top 15 vs {base:.0%} of everyone ranked")
keys_ok = all((c.key_videos > 0) == w.ch[c.channel_id]["keys"] for c in ranked)
print("accepts-keys detection matches the truth for every channel:", keys_ok)
lovers_ratio = [c.genre_ratio for c in ranked if c.genre_ratio and w.ch[c.channel_id]["love"]]
print(f"median 'views on similar games' ratio for lovers: {sorted(lovers_ratio)[len(lovers_ratio)//2]:.2f}×")
for c in ranked[:3]:
    print("  ", c.title, "|", c.reasons[0])
assert m.learned and love_top >= 0.8 and keys_ok
assert not any(w.ch[c.channel_id]["vids"][0]["age"] > 90 for c in ranked), "inactive filtered"
print("INTEGRATION TEST PASSED")

"""Looser game matching, 'why isn't X in the list?' and adding a channel by hand."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import scout
from fake_youtube import GAMES, World
from storage import Store
from youtube_api import YouTubeClient

m = scout.mentions
assert m("I tried Digging a Hole for 10 hours", "A Game About Digging A Hole")
assert m("A GAME ABOUT DIGGING A HOLE is insane", "A Game About Digging A Hole")
assert m("SteamWorld Dig 2 full playthrough", "SteamWorld Dig")
assert not m("Hydro dipping", "Hydroneer")
assert not m("deep rock climbing", "Deep Rock Galactic")
assert not m("I dug a hole", "A Game About Digging A Hole")     # different words: still not matched
print("looser game matching ✓")

w = World(seed=7)
client = YouTubeClient("K", Store(Path(tempfile.mkdtemp()) / "t.sqlite3"), w.session())
st = scout.Settings(game_name="Digger's End", game_description="digging simulator, mine ore", comparable_games=GAMES,
                    min_subs=500, max_subs=300_000, max_deep=40, max_inactive_days=90)
ranked, stats = scout.run(st, client)
allc = stats["all"]
by_stage = {}
for c in allc.values():
    by_stage.setdefault(c.stage, []).append(c)
print({k: len(v) for k, v in by_stage.items()})
for stage in ("filtered_size", "not_analysed", "inactive", "ranked"):
    assert by_stage.get(stage), stage
    print(f"  {stage:14} → {scout.explain(by_stage[stage][0], st)}")
never = next(cid for cid in w.ch if cid not in allc)
print("  not found      →", scout.explain(None, st, queries=stats["queries"])[:110], "…")

# add a channel that was found but not analysed
c0 = by_stage["not_analysed"][0]
before = client.units_used
added = scout.add_channel(c0.channel_id, client, st, ranked, allc)
assert added in ranked and added.added_manually and added.stage == "ranked" and added.reasons
print(f"add_channel: {added.title} added at #{ranked.index(added) + 1} of {len(ranked)} "
      f"({client.units_used - before} units)")
# and one that no search found at all
added2 = scout.add_channel(never, client, st, ranked, allc)
assert added2 in ranked and added2.recent
print(f"add_channel (never found): {added2.title} → chance {100 * added2.chance:.0f}%")
print("EXPLAIN TESTS PASSED")

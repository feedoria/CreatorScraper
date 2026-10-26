"""A viral hit that every big channel played must not push huge, off-genre channels to the top."""
import statistics, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import model, scout
from fake_youtube import GAMES, World
from storage import Store
from youtube_api import YouTubeClient

VIRAL = "A Game About Digging A Hole"

def run(old: bool, seed: int):
    w = World(seed=seed, n=160, viral=VIRAL)
    client = YouTubeClient("K", Store(Path(tempfile.mkdtemp()) / "t.sqlite3"), w.session())
    st = scout.Settings(game_name="Digger's End", game_description="digging simulator, mine ore, drills, caves",
                        comparable_games=GAMES, min_subs=500, max_subs=10_000_000, max_deep=150)
    saved = (model.game_weights, scout.reachability)
    if old:
        model.game_weights = lambda cands, games: {g: 1.0 for g in games}
        scout.reachability = lambda subs: 0.8 if not subs else 1 / (1 + (subs / 250_000) ** 0.8)
    try:
        ranked, stats = scout.run(st, client)
    finally:
        model.game_weights, scout.reachability = saved
    top = ranked[:15]
    mega = [c for c in ranked if c.subs and c.subs > 1_000_000]
    rank_mega = statistics.mean(ranked.index(c) for c in mega) if mega else 0
    return (sum(w.ch[c.channel_id]["love"] for c in top) / len(top),
            statistics.median(c.subs for c in top), stats["model"], rank_mega)

for seed in (3, 5):
    o_love, o_subs, _, o_mega = run(True, seed)
    n_love, n_subs, m, n_mega = run(False, seed)
    print(f"seed {seed}: top 15 — before: {o_love:.0%} genre fans, median {scout.fmt_num(o_subs)} subs"
          f"  |  now: {n_love:.0%} genre fans, median {scout.fmt_num(n_subs)} subs")
    print(f"   average position of 1M+ channels: before #{o_mega:.0f} → now #{n_mega:.0f}")
    assert n_mega > o_mega
    print(f"   game weights: {', '.join(f'{g.split()[0]}… {w:.2f}' if len(g) > 14 else f'{g} {w:.2f}' for g, w in m.weights.items())}")
    assert n_love >= o_love and n_subs <= o_subs
    assert m.weights[VIRAL] < 0.8 and min(v for g, v in m.weights.items() if g != VIRAL) > 0.85, m.weights
print("VIRAL TESTS PASSED")

"""Steam tags: store search, tag parsing from the store page (real markup shape), fallback, aggregation."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import steam
from storage import Store

def page(tags):
    a = "".join(f'<a href="https://store.steampowered.com/tags/en/{t.replace(" ", "%20")}/?snr=1_5_9__409" '
                f'class="app_tag" style="display: none;">\n\t\t\t\t\t\t\t\t\t\t\t\t{t}\t\t\t\t\t\t\t\t\t\t\t\t</a>' for t in tags)
    return (f'<div class="glance_tags popular_tags" data-appid="1">{a}'
            '<div class="app_tag add_button" onclick="ShowAppTagModal(1)">+</div></div>')

APPS = {"Hydroneer": (1106840, ["Simulation", "Mining", "Sandbox", "Building", "Indie", "Crafting"]),
        "Dome Keeper": (1637320, ["Roguelite", "Mining", "Indie", "Tower Defense", "Pixel Graphics"]),
        "Mining Mechanics": (2342590, ["Simulation", "Mining", "Automation", "Indie"]),
        "SteamWorld Dig": (252410, [])}           # page without tags -> SteamSpy fallback
class R:
    def __init__(s, body, js=None): s.status_code, s.text, s._js = 200, body, js
    def json(s): return s._js
class Sess:
    def __init__(s): s.calls = 0
    def get(s, url, params=None, headers=None, cookies=None, timeout=None):
        s.calls += 1
        assert cookies and cookies.get("birthtime"), "age-gate cookies must be sent"
        if "storesearch" in url:
            term = params["term"].lower()
            items = [{"id": i, "name": n} for n, (i, _) in APPS.items() if term in n.lower()]
            items += [{"id": 999, "name": "Hydroneer Soundtrack"}] if "hydroneer" in term else []
            return R("", {"total": len(items), "items": items})
        if "populartags" in url:
            return R("", [{"tagid": i, "name": n} for i, n in enumerate(["Mining", "Indie", "Simulation", "Zombies", "2D"] * 1 + [f"Tag{i}" for i in range(80)])])
        if "steamspy" in url:
            return R("", {"tags": {"Metroidvania": 900, "Mining": 1200, "Platformer": 300}})
        appid = int(url.split("/app/")[1].split("/")[0])
        tags = next(t for i, t in APPS.values() if i == appid)
        return R(f"<html><body>{page(tags)}</body></html>")

st = steam.Steam(Store(Path(tempfile.mkdtemp()) / "s.sqlite3"), Sess())
name, tags = st.tags_for("hydroneer")
assert name == "Hydroneer" and tags == APPS["Hydroneer"][1], (name, tags)
print("store page tags:", name, tags)
name, tags = st.tags_for("https://store.steampowered.com/app/1637320/Dome_Keeper/")
assert tags == APPS["Dome Keeper"][1], tags
print("from a store link:", tags)
_, tags = st.tags_for("SteamWorld Dig")
assert tags == ["Mining", "Metroidvania", "Platformer"], tags
print("SteamSpy fallback (sorted by votes):", tags)
counts, missing = st.tags_for_games(["Hydroneer", "Dome Keeper", "Mining Mechanics", "Totally Unknown Game"])
print("shared by comparable games:", counts.most_common(4), "missing:", missing)
assert counts["Mining"] == 3 and counts["Indie"] == 3 and missing == ["Totally Unknown Game"]
calls = st.s.calls
st.tags_for("hydroneer")
assert st.s.calls == calls, "second lookup must come from cache"
tags, live = st.all_tags()
assert live and len(tags) == 85 and tags[0] == "2D" and "Mining" in tags, (len(tags), tags[:3])
class Down:
    def get(s, *a, **k): raise ConnectionError("offline")
off, live = steam.Steam(None, Down()).all_tags()
assert not live and len(off) > 100 and "Mining" in off
print(f"all tags: {len(tags)} from Steam; offline fallback list: {len(off)} tags")
print("STEAM TESTS PASSED")

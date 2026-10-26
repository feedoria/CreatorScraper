"""Steam user tags for a game (its store page), to use as keywords.

- find_app("Hydroneer")             -> (appid, name) via the public store search
- app_tags(1106840)                 -> ["Simulation", "Mining", "Sandbox", …] in Steam's order
- tags_for_games([...names])        -> how many of your comparable games share each tag

Tags come from the store page (the same list you see under the screenshots);
SteamSpy is used as a fallback. Results are cached for a week.
"""
from __future__ import annotations

import html
import re
from collections import Counter

import requests

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_URL = "https://store.steampowered.com/app/{appid}/?l=english"
SPY_URL = "https://steamspy.com/api.php"
COOKIES = {"birthtime": "568022401", "lastagecheckage": "1-January-1988",
           "wants_mature_content": "1", "Steam_Language": "english"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CreatorScout/1.0",
           "Accept-Language": "en-US,en;q=0.8"}
TAG_RE = re.compile(r'<a[^>]+class="app_tag"[^>]*>\s*([^<]+?)\s*</a>', re.I)
APPID_RE = re.compile(r"store\.steampowered\.com/app/(\d+)", re.I)
TTL = 7 * 86400


ALL_TAGS_URL = "https://store.steampowered.com/tagdata/populartags/english"
# Used only if Steam can't be reached: the most common store tags.
FALLBACK_TAGS = """2D,2.5D,3D,3D Platformer,4X,Action,Action RPG,Action Roguelike,Action-Adventure,Adventure,
Agriculture,Alternate History,Arcade,Atmospheric,Automation,Base Building,Battle Royale,Beat 'em up,
Board Game,Building,Bullet Hell,Card Game,Cartoony,Casual,Character Customization,City Builder,
Clicker,Colony Sim,Colorful,Combat,Comedy,Competitive,Crafting,Creature Collector,Crime,Cute,Cyberpunk,
Dark,Dark Fantasy,Deckbuilding,Destruction,Detective,Difficult,Driving,Dungeon Crawler,Economy,
Education,Exploration,Family Friendly,Fantasy,Farming,Farming Sim,Fast-Paced,First-Person,Fishing,
Free to Play,Funny,Gore,Grid-Based Movement,Hack and Slash,Hand-drawn,Hidden Object,Horror,
Idler,Immersive Sim,Indie,Inventory Management,Isometric,JRPG,Life Sim,Local Co-Op,Local Multiplayer,
Logic,Loot,Management,Massively Multiplayer,Match 3,Medieval,Metroidvania,Mining,Minimalist,MMORPG,
Moddable,Multiplayer,Mystery,Nature,Online Co-Op,Open World,Open World Survival Craft,Physics,Pixel Graphics,
Platformer,Point & Click,Post-apocalyptic,Procedural Generation,Psychological Horror,Puzzle,
Puzzle Platformer,Racing,Realistic,Relaxing,Resource Management,Retro,Rhythm,Roguelike,Roguelite,
Role-Playing,RPG,RTS,Sandbox,Sci-fi,Shooter,Simulation,Singleplayer,Souls-like,Space,Sports,Stealth,
Story Rich,Strategy,Stylized,Survival,Survival Horror,Tactical,Third Person,Top-Down,Tower Defense,
Trading,Turn-Based,Turn-Based Strategy,Twin Stick Shooter,Underground,Vehicular Combat,Visual Novel,
Wholesome,Zombies""".replace("\n", "")


class SteamError(Exception):
    pass


class Steam:
    def __init__(self, store=None, session=None):
        self.store = store
        self.s = session or requests.Session()

    # -- small cached GET ------------------------------------------------------
    def _get(self, url, params=None, as_json=False):
        key = "steam:" + url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        if self.store is not None:
            hit = self.store.cache_get(key, TTL)
            if hit is not None:
                return hit["body"]
        try:
            r = self.s.get(url, params=params, headers=HEADERS, cookies=COOKIES, timeout=15)
        except Exception as e:
            raise SteamError(f"Could not reach Steam ({type(e).__name__})") from e
        if r.status_code != 200:
            raise SteamError(f"Steam answered {r.status_code}")
        try:
            body = r.json() if as_json else r.text
        except ValueError as e:
            raise SteamError("Steam sent an unexpected answer") from e
        if self.store is not None:
            self.store.cache_put(key, {"body": body})
        return body

    # -- public ----------------------------------------------------------------
    def find_app(self, name_or_link: str) -> tuple[int, str] | None:
        text = (name_or_link or "").strip()
        m = APPID_RE.search(text)
        if m:
            return int(m.group(1)), ""
        if text.isdigit():
            return int(text), ""
        if not text:
            return None
        body = self._get(SEARCH_URL, {"term": text, "l": "english", "cc": "US"}, as_json=True)
        items = (body or {}).get("items") or []
        if not items:
            return None
        want = re.sub(r"[^a-z0-9]", "", text.lower())
        exact = [i for i in items if re.sub(r"[^a-z0-9]", "", i.get("name", "").lower()) == want]
        best = (exact or items)[0]
        return int(best["id"]), best.get("name", "")

    def app_tags(self, appid: int) -> list[str]:
        page = self._get(APP_URL.format(appid=appid))
        tags = [html.unescape(t).strip() for t in TAG_RE.findall(page or "")]
        tags = [t for t in dict.fromkeys(tags) if t and t != "+"]
        if tags:
            return tags
        try:  # fallback: SteamSpy keeps tags with vote counts
            spy = self._get(SPY_URL, {"request": "appdetails", "appid": appid}, as_json=True)
            votes = (spy or {}).get("tags") or {}
            if isinstance(votes, dict):
                return [t for t, _ in sorted(votes.items(), key=lambda kv: -kv[1])]
        except SteamError:
            pass
        return []

    def all_tags(self) -> tuple[list[str], bool]:
        """Every Steam store tag, alphabetical. (tags, from_steam)"""
        try:
            body = self._get(ALL_TAGS_URL, as_json=True)
            names = sorted({t["name"].strip() for t in body or [] if isinstance(t, dict) and t.get("name")},
                           key=str.casefold)
            if len(names) > 50:
                return names, True
        except SteamError:
            pass
        return sorted({t.strip() for t in FALLBACK_TAGS.split(",") if t.strip()}, key=str.casefold), False

    def tags_for(self, name_or_link: str) -> tuple[str, list[str]]:
        found = self.find_app(name_or_link)
        if not found:
            raise SteamError(f"Couldn't find “{name_or_link}” on Steam")
        appid, name = found
        return name or f"app {appid}", self.app_tags(appid)

    def tags_for_games(self, names: list[str]) -> tuple[Counter, list[str]]:
        """Counter(tag -> how many of the games have it), plus the names not found."""
        counts, missing = Counter(), []
        for n in names:
            try:
                _, tags = self.tags_for(n)
            except SteamError:
                missing.append(n)
                continue
            counts.update(tags[:20])  # Steam shows the 20 most-voted tags
        return counts, missing

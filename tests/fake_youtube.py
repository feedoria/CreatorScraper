"""A simulated YouTube with a known truth, answering in the real API's JSON format."""
import random
from datetime import datetime, timedelta, timezone

GAMES = ["Hydroneer", "SteamWorld Dig", "Dome Keeper", "Deep Rock Galactic", "Mining Mechanics",
         "A Game About Digging A Hole"]
OTHER = ["Minecraft survival", "Barbie dreamhouse tour", "GTA 6 news", "Stardew Valley farm",
         "Fortnite season recap", "cozy cafe simulator", "Elden Ring boss", "Roblox obby"]
NOW = datetime.now(timezone.utc)
iso = lambda d: (NOW - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")


APPIDS = {g: 100000 + i for i, g in enumerate(GAMES)}


class World:
    def __init__(self, seed=1, n=120, n_videos=30, rich=False):
        """rich=True: long channels where coverage is often old, only in tags, or only a Steam link."""
        rnd = random.Random(seed)
        self.ch = {}
        for i in range(n):
            cid = f"UC{i:04d}"
            love = rnd.random() < 0.35
            keys = rnd.random() < (0.45 if love else 0.1)
            subs = int(10 ** rnd.uniform(3.2, 6.2))
            med = subs * rnd.uniform(0.04, 0.35)
            covered = [g for g in GAMES if rnd.random() < (0.45 if love else 0.04)]
            if not covered:                        # everybody was found through at least one game
                covered = [rnd.choice(GAMES)] if rnd.random() < 0.6 else []
            last = rnd.choice([1, 3, 8, 20, 45]) if rnd.random() < 0.9 else 200
            vids = []
            slots = rnd.sample(range(n_videos), len(covered)) if rich else list(range(len(covered)))
            for j in range(n_videos):
                tags, extra = [], ""
                if j in slots:
                    game = covered[slots.index(j)]
                    v = med * (rnd.uniform(1.3, 2.6) if love else rnd.uniform(0.4, 0.9))
                    style = rnd.random() if rich else 0.0
                    if style < 0.45:
                        t = f"{game} gameplay part {j + 1}"
                    elif style < 0.8:
                        t, tags = rnd.choice(["This game is INSANE", "I can't stop playing this", "Day 100"]), [game, "gameplay"]
                    else:
                        t, extra = "New favourite game?!", f" Get it: https://store.steampowered.com/app/{APPIDS[game]}/"
                else:
                    t = rnd.choice(["new indie digging simulator", "mining game with drills and ore", "underground cave crafting"]
                                   if love and rnd.random() < 0.5 else OTHER)
                    v = med * rnd.uniform(0.6, 1.4)
                d = ("Thanks to the devs for the key!" if keys and j == 2 else "Like and subscribe") + extra
                vids.append({"id": f"{cid}_{j}", "title": t, "desc": d, "views": int(v), "age": last + j * 4,
                             "tags": tags, "game": covered[slots.index(j)] if j in slots else None})
            self.ch[cid] = {"title": f"Channel{i}", "subs": subs, "love": love, "keys": keys, "covered": covered,
                            "desc": "Indie games, simulators and digging" if love else "Gaming and fun",
                            "vids": vids}

    def session(self):
        return Session(self)


class R:
    def __init__(self, body, code=200): self.status_code, self._b, self.text = code, body, ""
    def json(self): return self._b


class Session:
    def __init__(self, w): self.w = w
    def get(self, url, params, timeout):
        ep = url.rsplit("/", 1)[1]; w = self.w
        if ep == "search":
            game = params["q"].replace(" gameplay", "")
            items = []
            for cid, c in w.ch.items():
                for v in c["vids"]:
                    if v["title"].startswith(game + " "):
                        items.append({"id": {"videoId": v["id"]}, "snippet": {"channelId": cid, "channelTitle": c["title"], "title": v["title"]}})
            return R({"items": items[:50]})
        if ep == "channels":
            out = []
            for cid in params["id"].split(","):
                c = w.ch[cid]
                out.append({"id": cid, "snippet": {"title": c["title"], "description": c["desc"]},
                            "statistics": {"subscriberCount": str(c["subs"]), "viewCount": "1", "videoCount": "30"},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid}}, "brandingSettings": {"channel": {}}})
            return R({"items": out})
        if ep == "playlistItems":
            c = w.ch[params["playlistId"][2:]]
            start = int(params.get("pageToken", 0)); end = start + int(params.get("maxResults", 50))
            body = {"items": [{"snippet": {"title": v["title"], "description": v["desc"]},
                               "contentDetails": {"videoId": v["id"], "videoPublishedAt": iso(v["age"])}}
                              for v in c["vids"][start:end]]}
            if end < len(c["vids"]):
                body["nextPageToken"] = str(end)
            return R(body)
        if ep == "videos":
            items = []
            for vid in params["id"].split(","):
                cid, j = vid.split("_"); v = w.ch[cid]["vids"][int(j)]
                items.append({"id": vid, "snippet": {"tags": v.get("tags", [])}, "statistics": {"viewCount": str(v["views"])}})
            return R({"items": items})
        return R({"error": {"errors": [{"reason": "notFound"}], "message": "nf"}}, 404)

import sys, os, tempfile, random
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scout
from storage import Store
from youtube_api import YouTubeClient
from contacts import extract_emails, extract_links

NOW = datetime.now(timezone.utc)
def iso(days): return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

# channel_id: (title, subs, desc, video titles, days since last upload, views/video)
CH = {
 "UCdig": ("DeepDigger", 24000, "Indie sim games! Business: deepdigger [at] gmail [dot] com",
           ["Hydroneer is AMAZING - mining gold", "Dome Keeper first run", "Digging to the core in A Game About Digging A Hole", "mining simulator ore upgrades drill"], 3, 9000),
 "UCbarbie": ("PinkPlay", 80000, "Barbie dreamhouse dress up makeover fun! contact: pink@play.com",
           ["Barbie dreamhouse tour", "Barbie dress up challenge", "makeover party"], 2, 30000),
 "UCbig": ("MegaGamer", 9000000, "Huge channel. mega@agency.com", ["Hydroneer but every ore is lava", "GTA 6 news"], 1, 400000),
 "UCdead": ("OldMiner", 15000, "mining games", ["SteamWorld Dig 2 ending", "Dome Keeper"], 300, 2000),
 "UCmid": ("CozySimsHQ", 6000, "cozy simulator games, indie gems. Twitter: https://twitter.com/cozysims https://linktr.ee/cozysims",
           ["PowerWash Simulator relaxing", "Mining Mechanics factory", "new indie digging game"], 10, 1500),
}
SEARCH = {  # query -> channels returning that game in title
 "Hydroneer gameplay": [("UCdig","Hydroneer gold rush"),("UCbig","Hydroneer but lava"),("UCbarbie","Barbie vlog")],
 "Dome Keeper gameplay": [("UCdig","Dome Keeper first run"),("UCdead","Dome Keeper")],
 "SteamWorld Dig gameplay": [("UCdead","SteamWorld Dig 2 ending")],
 "Mining Mechanics gameplay": [("UCmid","Mining Mechanics factory")],
 "digging simulator": [("UCmid","new indie digging game"),("UCdig","Digging to the core")],
}

class Resp:
    def __init__(s, code, body): s.status_code, s._b, s.text = code, body, str(body)
    def json(s): return s._b

class FakeSession:
    def __init__(s, quota=10**9): s.calls, s.quota = [], quota
    def get(s, url, params, timeout):
        ep = url.rsplit("/",1)[1]; s.calls.append(ep)
        cost = 100 if ep=="search" else 1
        if s.quota < cost:
            return Resp(403, {"error":{"message":"quota","errors":[{"reason":"quotaExceeded"}]}})
        s.quota -= cost
        if ep=="search":
            return Resp(200, {"items":[{"id":{"videoId":f"v{cid}{i}"},"snippet":{"channelId":cid,"channelTitle":CH[cid][0],"title":t}}
                                       for i,(cid,t) in enumerate(SEARCH.get(params["q"],[]))]})
        if ep=="channels":
            out=[]
            for cid in params["id"].split(","):
                t,subs,desc,*_ = CH[cid]
                out.append({"id":cid,"snippet":{"title":t,"description":desc,"customUrl":"@"+t.lower()},
                            "statistics":{"subscriberCount":str(subs),"viewCount":"1","videoCount":"50","hiddenSubscriberCount":False},
                            "contentDetails":{"relatedPlaylists":{"uploads":"UU"+cid}},"brandingSettings":{"channel":{}}})
            return Resp(200,{"items":out})
        if ep=="playlistItems":
            cid = params["playlistId"][2:]; t,subs,desc,titles,last,v = CH[cid]
            items=[{"snippet":{"title":tt,"description":"Sponsor: https://amzn.to/x  business inquiries -> "+("biz@"+cid.lower()+".com" if cid=="UCmid" else "")},
                    "contentDetails":{"videoId":f"{cid}_{i}","videoPublishedAt":iso(last+i*4)}} for i,tt in enumerate(titles*4)]
            return Resp(200,{"items":items})
        if ep=="videos":
            items=[]
            for vid in params["id"].split(","):
                cid = vid.split("_")[0] if "_" in vid else vid[1:].rstrip("0123456789")
                items.append({"id":vid,"statistics":{"viewCount":str(CH[cid][5] * (3 if "_" not in vid else 1))}})
            return Resp(200,{"items":items})
        return Resp(404,{"error":{"message":"nf","errors":[{"reason":"notFound"}]}})

def settings():
    return scout.Settings(game_name="Digger's End",
        game_description="A digging simulator: dig underground with shovels and drills, mine ore and gems, sell at the shop, upgrade tools, mine cart tracks, cave-ins and moles.",
        comparable_games=["Hydroneer","Dome Keeper","SteamWorld Dig","Mining Mechanics"],
        keywords=["digging simulator"], min_subs=500, max_subs=5_000_000, max_inactive_days=90)

def main():
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp/"t.sqlite3")
    sess = FakeSession()
    cl = YouTubeClient("KEY", store, sess)
    ranked, stats = scout.run(settings(), cl, lambda m,f=None: None)
    names = [c.title for c in ranked]
    print("RANK:", [(c.title, c.total) for c in ranked]); print("STATS:", stats)
    assert names[0] == "DeepDigger", names
    assert "OldMiner" not in names, "inactive must be filtered"
    assert "MegaGamer" not in names, "over max subs must be filtered"
    assert names.index("PinkPlay") == len(names)-1, "barbie must be last"
    dd = ranked[0]
    assert dd.emails[0]["email"]=="deepdigger@gmail.com" and dd.emails[0]["business"], dd.emails
    assert dd.matched_games >= {"Hydroneer","Dome Keeper"}, dd.matched_games
    cozy = next(c for c in ranked if c.title=="CozySimsHQ")
    assert any(l["kind"]=="linktree" for l in cozy.links) and any(e["email"]=="biz@ucmid.com" for e in cozy.emails)
    assert not any("amzn" in l["url"] for l in cozy.links)
    for c in ranked: print(" ", c.title, "|", " / ".join(c.reasons))
    print(scout.draft_email(dd, {"game_name":"Digger's End","game_description":"Dig deep.","hook":"digging"}))
    assert stats["units"] == 5*100 + 1 + 4*2, stats["units"]  # 5 searches, 1 channels batch, 4 deep x2

    # second run: everything from cache -> 0 units
    cl2 = YouTubeClient("KEY", store, FakeSession())
    _, st2 = scout.run(settings(), cl2, lambda m,f=None: None)
    assert st2["units"] == 0 and st2["cache_hits"] > 0, st2

    # quota runs out mid-search -> partial results, no crash
    store.cache_clear()
    cl3 = YouTubeClient("KEY", store, FakeSession(quota=250))
    r3, st3 = scout.run(settings(), cl3, lambda m,f=None: None)
    assert st3["quota_hit"] and len(r3) > 0, st3
    print("QUOTA RUN:", st3, [c.title for c in r3])

    # exclude contacted
    s = settings(); s.exclude_ids={"UCdig"}
    store.cache_clear()
    r4,_ = scout.run(s, YouTubeClient("KEY", store, FakeSession()), lambda m,f=None: None)
    assert "DeepDigger" not in [c.title for c in r4]

    # contacts edge cases
    em = extract_emails(["Collabs: john(at)site(dot)io", "logo@2x.png noreply@x.com", "Business email: A.B@Mail.Co.UK"])
    assert [e["email"] for e in em] == ["a.b@mail.co.uk","john@site.io"], em
    print("ALL TESTS PASSED")
main()

import sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enrich
from scout import Candidate, reasons
from storage import Store

def cf_encode(email, key=0x42):
    return f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in email)

PAGES = {
 "https://linktr.ee/digger": '<html><body><a href="mailto:Digger.Biz@gmail.com?subject=hi">Email me</a>' + "word " * 80 + "</body></html>",
 "https://cozysims.net/": '<html><body><nav><a href="/pages/contact-us">Contact</a><a href="/blog">Blog</a></nav>' + "cozy games " * 50 + "</body></html>",
 "https://cozysims.net/pages/contact-us": '<p>Business inquiries: cozy [at] sims [dot] net</p>',
 "https://cfsite.com/": f'<p>Reach me: <a href="/cdn-cgi/l/email-protection" class="__cf_email__" data-cfemail="{cf_encode("pr@cfsite.com")}">[email protected]</a></p>' + "x " * 80,
 "https://private.dev/": "<p>contact: hidden@private.dev</p>",
 "https://private.dev/robots.txt": "User-agent: *\nDisallow: /",
 "https://jsapp.io/": '<html><head>' + '<script src="a.js"></script>' * 12 + '</head><body><div id="root"></div></body></html>',
}
class R:
    def __init__(s, url):
        s.url = url; body = PAGES.get(url)
        s.status_code = 200 if body is not None else 404
        s.text = body or ""; s.encoding = "utf-8"
        s.headers = {"Content-Type": "text/plain" if url.endswith("robots.txt") else "text/html; charset=utf-8"}
    def iter_content(s, n): yield s.text.encode()
    def close(s): pass
class Sess:
    def __init__(s): s.headers = {}; s.calls = []; s.lock = threading.Lock()
    def get(s, url, **kw):
        with s.lock: s.calls.append(url)
        return R(url)

class FakeDriver:
    def __init__(s): s.quit_called = False; s.url = None
    def get(s, url): s.url = url
    def execute_script(s, js): return "complete"
    @property
    def page_source(s): return '<div>Rendered by JS: collabs@jsapp.io</div>'
    def quit(s): s.quit_called = True

def cand(cid, links, emails=None):
    c = Candidate(channel_id=cid, title=cid); c.links = links; c.emails = emails or []
    c.scores = {"content": 1, "coverage": 1, "size": 1, "engagement": 1, "activity": 1}
    return c

def main():
    store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")
    cs = [
      cand("digger", [{"kind": "twitter", "url": "https://twitter.com/digger"}, {"kind": "linktree", "url": "https://linktr.ee/digger"}]),
      cand("cozy", [{"kind": "website", "url": "https://cozysims.net/"}]),
      cand("cf", [{"kind": "website", "url": "https://cfsite.com/"}]),
      cand("private", [{"kind": "website", "url": "https://private.dev/"}]),
      cand("js", [{"kind": "website", "url": "https://jsapp.io/"}]),
      cand("insta", [{"kind": "instagram", "url": "https://instagram.com/x"}]),
      cand("has", [{"kind": "website", "url": "https://cozysims.net/"}], [{"email": "a@b.com", "business": True, "source": "YouTube"}]),
    ]
    sess = Sess(); drv = FakeDriver()
    st = enrich.enrich(cs, store, top_n=50, use_browser=True, session=sess, browser_factory=lambda: drv)
    by = {c.channel_id: c for c in cs}
    print(st)
    for c in cs: print(" ", c.channel_id, [(e["email"], e["source"]) for e in c.emails])
    assert by["digger"].emails[0]["email"] == "digger.biz@gmail.com" and by["digger"].emails[0]["source"] == "Linktree"
    assert by["cozy"].emails[0]["email"] == "cozy@sims.net" and by["cozy"].emails[0]["source"] == "website"
    assert by["cf"].emails[0]["email"] == "pr@cfsite.com"
    assert by["private"].emails == [], "robots.txt must be respected"
    assert "https://private.dev/" not in sess.calls
    assert by["js"].emails[0]["email"] == "collabs@jsapp.io" and by["js"].emails[0]["source"] == "browser"
    assert drv.quit_called
    assert by["insta"].emails == [] and not any("instagram" in u or "twitter" in u for u in sess.calls)
    assert by["has"].emails[0]["email"] == "a@b.com"
    assert st["found_http"] == 3 and st["found_browser"] == 1, st
    print("  reason:", reasons(by["digger"])[-1])
    # second run is served from cache: no page requests
    for c in cs[:5]: c.emails = []
    sess2 = Sess()
    enrich.enrich(cs, store, use_browser=False, session=sess2)
    assert not [u for u in sess2.calls if not u.endswith("robots.txt")], sess2.calls
    # browser unavailable -> graceful
    for c in cs[:5]: c.emails = []
    st3 = enrich.enrich(cs, store, use_browser=True, session=Sess(), browser_factory=lambda: None)
    assert st3["browser"] == "unavailable" and by["js"].emails == []
    # a site that blows up with the exact error seen on Windows must only skip that site
    class BadSess(Sess):
        def get(s, url, **kw):
            if "weird.ro" in url:
                raise UnicodeDecodeError("utf-8", b"\xc5" * 1500, 1474, 1475, "invalid continuation byte")
            return super().get(url, **kw)
    cs2 = [cand("weird", [{"kind": "website", "url": "https://weird.ro/"}]),
           cand("digger2", [{"kind": "linktree", "url": "https://linktr.ee/digger"}])]
    st4 = enrich.enrich(cs2, Store(Path(tempfile.mkdtemp()) / "t2.sqlite3"), session=BadSess())
    assert cs2[0].emails == [] and cs2[1].emails[0]["email"] == "digger.biz@gmail.com", st4
    print("  bad-encoding site skipped, others still found ✓")
    print("ENRICH TESTS PASSED")
main()

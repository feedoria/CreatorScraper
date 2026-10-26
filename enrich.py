"""Find contact emails on creators' own public pages (Linktree, Beacons, Carrd,
personal websites) for ranked creators that have no email yet.

Step 1 (fast, always): plain HTTP with `requests` — several pages in parallel.
Step 2 (optional):     a headless browser via Selenium (Edge, else Chrome) for
                       pages whose content is built by JavaScript.

Rules: only links the creator published themselves, robots.txt respected,
at most a few pages per creator, results cached. Social networks that need
a login (X/Twitter, Instagram, TikTok) are never fetched.
"""
from __future__ import annotations

import html
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from contacts import CONF_ORDER, extract_emails, rate_email

FOLLOW_KINDS = ("linktree", "website")
NEVER_HOSTS = ("twitter.com", "x.com", "instagram.com", "tiktok.com", "facebook.com",
               "discord.gg", "discord.com", "twitch.tv", "patreon.com", "youtube.com",
               "youtu.be", "reddit.com", "steamcommunity.com", "store.steampowered.com")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 CreatorScout/1.0")
PAGE_TTL = 7 * 86400
MAX_BYTES = 1_500_000
CONTACT_HINT = re.compile(r"contact|about|business|press|collab|work-with|media|kontakt", re.I)

MAILTO_RE = re.compile(r"mailto:([^\"'?#>\s]+)", re.I)
CFEMAIL_RE = re.compile(r"data-cfemail=[\"']([0-9a-fA-F]+)[\"']|email-protection#([0-9a-fA-F]+)")
HREF_RE = re.compile(r"href=[\"']([^\"'#]+)[\"']", re.I)
SCRIPT_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


# ------------------------------------------------------------------ parsing
def cf_decode(hexstr: str) -> str:
    """Cloudflare 'email protection' obfuscation (XOR with the first byte)."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def page_text(raw: str) -> str:
    return html.unescape(TAG_RE.sub(" ", SCRIPT_RE.sub(" ", raw)))


def emails_in_html(raw: str) -> list[dict]:
    """Emails from mailto links, Cloudflare-protected addresses and visible text."""
    extra = []
    for m in MAILTO_RE.finditer(raw):
        extra.append("contact: " + html.unescape(m.group(1)))
    for m in CFEMAIL_RE.finditer(raw):
        dec = cf_decode(m.group(1) or m.group(2))
        if dec:
            extra.append("contact: " + dec)
    return extract_emails(extra + [page_text(raw)])


def looks_js_rendered(raw: str) -> bool:
    """Little visible text but lots of script = content probably built by JavaScript."""
    visible = len(page_text(raw).split())
    scripts = len(re.findall(r"<script\b", raw, re.I))
    return visible < 60 or (scripts > 8 and visible < 200) or "enable javascript" in raw.lower()


def contact_subpages(base_url: str, raw: str, limit: int = 2) -> list[str]:
    host = urlparse(base_url).netloc
    found = []
    for m in HREF_RE.finditer(raw):
        url = urljoin(base_url, html.unescape(m.group(1)))
        p = urlparse(url)
        if p.scheme in ("http", "https") and p.netloc == host and CONTACT_HINT.search(p.path):
            norm = url.split("?")[0].rstrip("/")
            if norm not in found and norm != base_url.rstrip("/"):
                found.append(norm)
    if not found:
        found = [urljoin(base_url, "/contact")]
    return found[:limit]


def followable(link: dict) -> bool:
    host = urlparse(link["url"]).netloc.lower()
    return link["kind"] in FOLLOW_KINDS and not any(h in host for h in NEVER_HOSTS)


# ------------------------------------------------------------------ fetching
class Fetcher:
    """Polite HTTP fetcher: robots.txt, size limit, timeout, cache in the app's store."""

    def __init__(self, store, session=None):
        self.store = store
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en,*;q=0.5"})
        self._robots: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            rp = None
            try:
                r = self.session.get(root + "/robots.txt", timeout=6)
                if r.status_code == 200 and "text" in r.headers.get("Content-Type", "text"):
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
            except Exception:  # network error, odd encoding, broken robots.txt …
                rp = None
            self._robots[root] = rp
        rp = self._robots[root]
        try:
            return True if rp is None else rp.can_fetch("CreatorScout", url)
        except Exception:
            return True

    def get(self, url: str) -> str | None:
        """HTML of a page, or None (blocked by robots, error, not HTML)."""
        key = "page:" + url
        cached = self.store.cache_get(key, PAGE_TTL)
        if cached is not None:
            return cached.get("html")
        body = None
        if self.allowed(url):
            try:
                r = self.session.get(url, timeout=8, stream=True, allow_redirects=True)
                ctype = r.headers.get("Content-Type", "")
                if r.status_code == 200 and ("html" in ctype or not ctype):
                    chunks, size = [], 0
                    for chunk in r.iter_content(65536):
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > MAX_BYTES:
                            break
                    body = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
                r.close()
            except Exception:  # incl. requests' UnicodeDecodeError on badly encoded redirects
                body = None
        self.store.cache_put(key, {"html": body})
        return body


# ------------------------------------------------------------------ browser
def find_browser_exe() -> str | None:
    """Edge or Chrome already installed on this computer (Edge ships with every Windows)."""
    import os
    import shutil
    env = os.environ.get
    candidates = []
    for base in (env("ProgramFiles(x86)"), env("ProgramFiles"), env("LOCALAPPDATA")):
        if base:
            candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                           os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    candidates += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for c in candidates:
        if os.path.isfile(c):
            return c
    for name in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


class DumpDomBrowser:
    """Runs the installed Edge/Chrome headless with --dump-dom: the page's HTML after its
    JavaScript ran. No WebDriver, nothing to download, separate temporary profile."""

    def __init__(self, exe: str):
        import tempfile
        self.exe = exe
        self.profile = tempfile.mkdtemp(prefix="creatorscout-browser-")

    def fetch(self, url: str, budget_ms: int = 6000, timeout: int = 30) -> str | None:
        args = [self.exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--mute-audio", "--disable-extensions", f"--user-data-dir={self.profile}",
                f"--user-agent={UA}", f"--virtual-time-budget={budget_ms}", "--dump-dom", url]
        import os
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.insert(1, "--no-sandbox")  # Linux as root only; never needed on Windows
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
        try:
            r = subprocess.run(args, capture_output=True, timeout=timeout, **kw)
        except Exception:
            return None
        out = r.stdout.decode("utf-8", errors="replace")
        return out if "<" in out else None

    def quit(self):
        import shutil
        shutil.rmtree(self.profile, ignore_errors=True)


def make_browser():
    """A way to render JavaScript pages, most robust first:
    1. the installed Edge/Chrome in headless --dump-dom mode (no driver needed),
    2. Selenium WebDriver (Edge, then Chrome; Selenium Manager fetches the driver).
    None if neither works; the reason is kept in LAST_BROWSER_ERROR."""
    global LAST_BROWSER_ERROR
    errors = []
    exe = find_browser_exe()
    if exe:
        b = DumpDomBrowser(exe)
        probe = b.fetch("data:text/html,<html><body><p id=x></p><script>document.getElementById('x')"
                        ".textContent='ok-'+(1+1)</script></body></html>", 1000, timeout=20)
        if probe and "ok-2" in probe:
            return b
        b.quit()
        errors.append(f"headless {exe}: no output")
    else:
        errors.append("no Edge/Chrome executable found")
    try:
        from selenium import webdriver
    except ImportError as e:
        LAST_BROWSER_ERROR = " | ".join(errors + [f"selenium not installed ({e})"])
        return None
    for name in ("edge", "chrome"):
        for with_service in (True, False):
            try:
                opts = webdriver.EdgeOptions() if name == "edge" else webdriver.ChromeOptions()
                for a in ("--headless=new", "--disable-gpu", "--log-level=3", "--mute-audio", f"--user-agent={UA}"):
                    opts.add_argument(a)
                opts.page_load_strategy = "eager"
                kw = {}
                if with_service:
                    if name == "edge":
                        from selenium.webdriver.edge.service import Service
                    else:
                        from selenium.webdriver.chrome.service import Service
                    service = Service()
                    if hasattr(subprocess, "CREATE_NO_WINDOW"):  # no console flash in the .exe
                        service.creation_flags = subprocess.CREATE_NO_WINDOW
                    kw["service"] = service
                drv = (webdriver.Edge if name == "edge" else webdriver.Chrome)(options=opts, **kw)
                drv.set_page_load_timeout(15)
                return drv
            except Exception as e:
                errors.append(f"selenium {name}{'' if with_service else ' (default service)'}: "
                              f"{type(e).__name__}: {str(e)[:300]}")
    LAST_BROWSER_ERROR = " | ".join(errors)
    return None


def browser_html(driver, url: str, wait: float = 2.0) -> str | None:
    if hasattr(driver, "fetch"):
        return driver.fetch(url)
    try:
        driver.get(url)
        end = time.time() + 8
        while time.time() < end and driver.execute_script("return document.readyState") != "complete":
            time.sleep(0.2)
        time.sleep(wait)  # let scripts fill in the page
        return driver.page_source
    except Exception:
        return None


def _rated(c, emails: list[dict], source: str, url: str) -> list[dict]:
    """Keep only addresses that can belong to this creator, best first."""
    handle = (getattr(c, "custom_url", "") or "").lstrip("@")
    out = []
    for e in emails:
        conf, why = rate_email(e["email"], c.title, handle, page_url=url, business=e.get("business", False))
        if conf:
            out.append({**e, "source": source, "url": url, "conf": conf, "why": why})
    return sorted(out, key=lambda e: (CONF_ORDER.get(e["conf"], 9), not e.get("business")))


LAST_BROWSER_ERROR = ""

# ------------------------------------------------------------------ main step
def enrich(candidates, store, top_n: int = 50, use_browser: bool = False,
           progress=lambda msg, frac=None: None, cancel=None, session=None,
           browser_factory=make_browser) -> dict:
    """Adds emails (with a `source`) to candidates[:top_n] that have none.
    Returns stats. Never raises for network problems."""
    todo = [c for c in candidates[:top_n] if not c.emails and any(followable(l) for l in c.links)]
    stats = {"checked": len(todo), "found_http": 0, "found_browser": 0, "pages": 0,
             "browser": "off"}
    if not todo:
        return stats
    fetcher = Fetcher(store, session)
    js_pages: dict[str, list[str]] = {}  # channel_id -> pages worth a browser try

    def work(c):
        try:
            return _work(c)
        except Exception:  # one strange page must never stop the whole search
            return c, 0, [], []

    def _work(c):
        pages, found, js = 0, [], []
        for link in [l for l in c.links if followable(l)][:3]:
            if cancel is not None and cancel.is_set():
                break
            url = link["url"]
            raw = fetcher.get(url)
            pages += 1
            if raw is None:
                continue
            em = emails_in_html(raw)
            src = "Linktree" if link["kind"] == "linktree" else "website"
            if not _rated(c, em, src, url) and link["kind"] == "website":
                for sub in contact_subpages(url, raw):
                    sub_raw = fetcher.get(sub)
                    pages += 1
                    if sub_raw and _rated(c, emails_in_html(sub_raw), src, sub):
                        em, url = emails_in_html(sub_raw), sub
                        break
            if em:
                found = _rated(c, em, src, url)
                if found:
                    break
            if looks_js_rendered(raw):
                js.append(url)
        return c, pages, found, js

    done = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        for fut in as_completed([pool.submit(work, c) for c in todo]):
            c, pages, found, js = fut.result()
            done += 1
            stats["pages"] += pages
            if found:
                c.emails = found
                stats["found_http"] += 1
            elif js:
                js_pages[c.channel_id] = js
            progress(f"Looking for emails on creators' pages ({done}/{len(todo)})…", done / len(todo))

    if use_browser and js_pages and not (cancel is not None and cancel.is_set()):
        progress(f"Opening a hidden browser for {len(js_pages)} JavaScript pages…")
        driver = browser_factory()
        if driver is None:
            stats["browser"] = "unavailable"
            stats["browser_error"] = LAST_BROWSER_ERROR
            progress("⚠ Browser fallback unavailable (Selenium, Edge or Chrome not found) — skipped.")
        else:
            stats["browser"] = "used"
            by_id = {c.channel_id: c for c in todo}
            try:
                for i, (cid, urls) in enumerate(js_pages.items(), 1):
                    if cancel is not None and cancel.is_set():
                        break
                    progress(f"Browser: checking page {i}/{len(js_pages)}…")
                    for url in urls:
                        if not fetcher.allowed(url):
                            continue
                        raw = browser_html(driver, url)
                        em = emails_in_html(raw) if raw else []
                        rated = _rated(by_id[cid], em, "browser", url) if em else []
                        if rated:
                            by_id[cid].emails = rated
                            stats["found_browser"] += 1
                            break
            finally:
                try:
                    driver.quit()
                except Exception:
                    pass
    return stats

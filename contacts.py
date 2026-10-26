"""Extract publicly posted contact info from text the creator published
(channel description, video descriptions). Nothing behind a login/CAPTCHA."""
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)

_AT = re.compile(r"\s*[\[\(\{<]\s*(?:at|@)\s*[\]\)\}>]\s*", re.I)
_DOT = re.compile(r"\s*[\[\(\{<]\s*(?:dot|\.)\s*[\]\)\}>]\s*", re.I)

BUSINESS_HINT = re.compile(
    r"business|contact|inquir|enquir|sponsor|collab|biz|management|booking|press|pr\b|keys?\b|email|mail",
    re.I,
)
JUNK_DOMAINS = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yourname")
JUNK_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
JUNK_LOCAL = ("noreply", "no-reply", "donotreply")

LINK_KINDS = [
    ("twitter", ("twitter.com", "x.com")),
    ("instagram", ("instagram.com",)),
    ("tiktok", ("tiktok.com",)),
    ("discord", ("discord.gg", "discord.com")),
    ("twitch", ("twitch.tv",)),
    ("linktree", ("linktr.ee", "beacons.ai", "carrd.co", "bio.link", "solo.to")),
    ("patreon", ("patreon.com",)),
    ("steam", ("steamcommunity.com", "store.steampowered.com")),
]
IGNORED_LINK_HOSTS = ("youtube.com", "youtu.be", "amzn.to", "amazon.", "bit.ly/", "geni.us")


def deobfuscate(text: str) -> str:
    return _DOT.sub(".", _AT.sub("@", text or ""))


def extract_emails(texts: list[str]) -> list[dict]:
    """Return unique emails, business-looking ones first:
    [{'email':..., 'business': bool}]"""
    found: dict[str, bool] = {}
    for raw in texts:
        text = deobfuscate(raw)
        for m in EMAIL_RE.finditer(text):
            e = m.group(0).strip(".").lower()
            local, _, domain = e.partition("@")
            if e.endswith(JUNK_SUFFIX) or any(j in domain for j in JUNK_DOMAINS):
                continue
            if any(j in local for j in JUNK_LOCAL):
                continue
            context = text[max(0, m.start() - 80) : m.start()]
            biz = bool(BUSINESS_HINT.search(context))
            found[e] = found.get(e, False) or biz
    return [
        {"email": e, "business": b}
        for e, b in sorted(found.items(), key=lambda kv: (not kv[1], kv[0]))
    ]


def extract_links(texts: list[str], limit: int = 12) -> list[dict]:
    seen, out = set(), []
    for raw in texts:
        for m in URL_RE.finditer(raw or ""):
            url = m.group(0).rstrip(".,;:!")
            low = url.lower()
            if any(h in low for h in IGNORED_LINK_HOSTS):
                continue
            norm = low.split("?")[0].rstrip("/")
            if norm in seen:
                continue
            seen.add(norm)
            kind = "website"
            for k, hosts in LINK_KINDS:
                if any(h in low for h in hosts):
                    kind = k
                    break
            out.append({"kind": kind, "url": url})
    # socials/linktree before random websites, keep order otherwise
    order = {k: i for i, (k, _) in enumerate(LINK_KINDS)}
    out.sort(key=lambda l: order.get(l["kind"], 99))
    return out[:limit]


# ---------------------------------------------------------------- precision
# Which links belong to the creator, and how believable an email is.

FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
             "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
             "pm.me", "gmx.com", "gmx.de", "gmx.net", "aol.com", "yandex.com", "yandex.ru", "mail.ru",
             "zoho.com", "web.de", "hotmail.co.uk", "yahoo.co.uk", "outlook.de", "seznam.cz", "wp.pl"}
# helpdesks, platforms, game companies, link services: never a creator's own inbox
BAD_DOMAIN_PARTS = ("zendesk", "freshdesk", "helpscout", "intercom", "zohodesk", "kayako", "desk.com",
                    "sentry", "example.", "wixpress", "godaddy", "squarespace", "shopify", "cloudflare",
                    "google.", "youtube.", "apple.", "steampowered", "valvesoftware", "epicgames",
                    "discord", "twitch.tv", "patreon", "linktr.ee", "beacons.ai", "amazon", "paypal",
                    "stripe", "microsoft", "playstation", "sony.", "nintendo", "ea.com", "ubisoft",
                    "twitter", "tiktok", "instagram", "facebook", "fandom", "github", "humblebundle",
                    "gog.com", "itch.io", "kickstarter", "streamelements", "streamlabs", "gamesight",
                    "keymailer", "woovit", "lurkit")
BAD_LOCAL_RE = re.compile(
    r"^(support|help|helpdesk|customer\w*|service|noreply|no-reply|donotreply|privacy|legal|abuse|dmca|"
    r"copyright|billing|invoices?|accounts?|orders?|shop|store|merch\w*|sales|careers|jobs|hr|admin|"
    r"webmaster|postmaster|hostmaster|security|returns?|refunds?|feedback|newsletter|unsubscribe|"
    r"bugs?|qa|community|moderation|appeals?)([._+-]|$)", re.I)
AGENCY_HINT = re.compile(r"management|agency|talent|partnership|business|biz|collab|sponsor|booking|"
                         r"inquir|enquir|press|media|brand", re.I)
GENERIC_NAME_WORDS = {"the", "and", "gaming", "games", "game", "gamer", "official", "channel", "plays",
                      "play", "tv", "yt", "youtube", "live", "stream", "streams", "videos", "video", "mr",
                      "real", "its", "just", "with"}
LINK_SERVICES = ("linktr.ee", "beacons.ai", "carrd.co", "bio.link", "solo.to")


def registered_domain(host: str) -> str:
    parts = (host or "").lower().split(":")[0].strip(".").split(".")
    if parts and parts[0] == "www":
        parts = parts[1:]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def name_tokens(title: str, handle: str = "") -> set[str]:
    words = re.findall(r"[a-z0-9]+", f"{title} {handle}".lower())
    toks = {w for w in words if len(w) >= 4 and w not in GENERIC_NAME_WORDS}
    joined = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    if len(joined) >= 4:
        toks.add(joined)
    h = re.sub(r"[^a-z0-9]", "", (handle or "").lower())
    if len(h) >= 4:
        toks.add(h)
    return toks


def rate_email(email: str, title: str, handle: str = "", page_url: str = "",
               in_channel_description: bool = False, business: bool = False):
    """(confidence, reason) with confidence in {'high','medium','low'}, or (None, reason)
    when the address is clearly not the creator's (helpdesk, merch, platform…)."""
    local, _, domain = email.lower().partition("@")
    if any(b in domain for b in BAD_DOMAIN_PARTS):
        return None, "a helpdesk / platform / company address"
    if BAD_LOCAL_RE.match(local):
        return None, f"a '{local.split('.')[0]}' address (support, merch, orders…), not for collaborations"
    compact = re.sub(r"[^a-z0-9]", "", local + domain.split(".")[0])
    if any(t in compact for t in name_tokens(title, handle)):
        return "high", "the address contains the channel's name"
    host = (page_url.split("//", 1)[-1].split("/", 1)[0]) if page_url else ""
    if host and not any(s in host for s in LINK_SERVICES) and registered_domain(domain) == registered_domain(host):
        return "high", "on the creator's own website, same domain"
    if in_channel_description:
        return ("high" if business else "medium"), "written in their own channel description"
    if domain in FREE_MAIL:
        return "medium", "a personal mailbox, but the name doesn't match the channel"
    if AGENCY_HINT.search(local + "." + domain):
        return "medium", "looks like a management / agency address"
    return "low", "unrelated domain — check before using"


CONF_ORDER = {"verified": 0, "high": 1, "medium": 2, "low": 3}


def own_links(channel_description: str, video_descriptions: list[str], min_repeats: int = 3,
              limit: int = 12) -> list[dict]:
    """Links the creator owns: those in the channel description, plus links that repeat in
    several video descriptions (their standard footer). One-off links in a single video are
    usually sponsors, games or other creators — never followed."""
    links = extract_links([channel_description], limit=50)
    seen = {l["url"].lower().split("?")[0].rstrip("/") for l in links}
    counts: dict[str, int] = {}
    first: dict[str, dict] = {}
    for d in video_descriptions:
        for l in extract_links([d], limit=50):
            k = l["url"].lower().split("?")[0].rstrip("/")
            counts[k] = counts.get(k, 0) + 1
            first.setdefault(k, l)
    for k, n in counts.items():
        if n >= min_repeats and k not in seen:
            links.append(first[k])
            seen.add(k)
    order = {k: i for i, (k, _) in enumerate(LINK_KINDS)}
    links.sort(key=lambda l: order.get(l["kind"], 99))
    return [l for l in links if l["kind"] != "steam"][:limit]

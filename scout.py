"""Discovery + ranking pipeline (GUI-independent, testable with a fake client).

1. Discover: search videos about comparable games + genre keywords.
2. Enrich:   channel stats (cheap), recent uploads + their views (cheap).
3. Signals:  content match (TF-IDF + pretrained semantic model), similar games
             played, how those videos performed, accepted dev keys, size,
             engagement, activity.
4. Predict:  a coverage model learned from this genre's public YouTube data
             (model.py) -> coverage chance, predicted views, expected views.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import model as cov_model
from contacts import CONF_ORDER, extract_emails, own_links, rate_email
from youtube_api import QuotaExceeded

WEIGHTS = {"content": 0.35, "coverage": 0.30, "size": 0.15, "engagement": 0.10, "activity": 0.10}

STOPWORDS = set(
    """a an and are as at be but by for from has have i if in into is it its me my of on or our
    so that the their this to was we with you your yours they them he she his her not no all
    just out up new vs part ep episode live stream video videos full let lets let's play playing
    gameplay game games first time day days how what why when best ever more most one two get got
    will can like do does did 2020 2021 2022 2023 2024 2025 2026 pt shorts short official trailer
    channel subscribe welcome here there were been am other some only about over again""".split()
)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]{1,}")
KEY_RE = re.compile(
    r"\b(?:steam\s+)?(?:key|copy|code|review copy|press copy)s?\b[^.\n]{0,40}?\b(?:was\s+|were\s+)?"
    r"(?:provided|given|sent|supplied|gifted|courtesy)\b"
    r"|\bthanks?(?:\s+you)?\b[^.\n]{0,60}?\bfor\s+(?:the|a|providing\s+(?:the|a))\s+(?:steam\s+)?(?:key|copy|code)\b"
    r"|\b(?:provided|gifted|sent)\s+(?:to\s+me\s+)?by\s+the\s+(?:developer|publisher|dev|studio)s?\b",
    re.I)
SPONSOR_RE = re.compile(r"\bsponsored\s+by\b|\bin\s+partnership\s+with\b|\bpaid\s+promotion\b|#ad\b|#sponsored\b", re.I)


@dataclass
class Settings:
    game_name: str
    game_description: str
    comparable_games: list[str]
    keywords: list[str] = field(default_factory=list)
    min_subs: int = 500
    max_subs: int = 2_000_000
    max_inactive_days: int = 90
    pages_per_query: int = 1
    max_deep: int = 150
    language: str = ""  # e.g. "en"; empty = any
    exclude_ids: set[str] = field(default_factory=set)
    use_semantic: bool = True
    tags: list[str] = field(default_factory=list)   # selected Steam/manual tags: used for matching
    videos_per_channel: int = 100                   # uploads analysed per channel (1 unit / 50)
    game_appids: dict = field(default_factory=dict) # comparable game -> Steam appid (for store links)


@dataclass
class Candidate:
    channel_id: str
    title: str = ""
    custom_url: str = ""
    description: str = ""
    country: str = ""
    keywords_text: str = ""
    subs: int | None = None
    total_views: int = 0
    video_count: int = 0
    uploads_pid: str = ""
    found_via: Counter = field(default_factory=Counter)
    matched_games: set = field(default_factory=set)
    recent: list = field(default_factory=list)
    last_upload: datetime | None = None
    median_views: float | None = None
    emails: list = field(default_factory=list)
    links: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    total: float = 0.0
    reasons: list = field(default_factory=list)
    # signals for the prediction
    hit_videos: dict = field(default_factory=dict)   # game -> video ids from search
    game_views: dict = field(default_factory=dict)   # game -> views of their videos on it
    key_videos: int = 0
    sponsor_videos: int = 0
    sem_sim: float = 0.0
    kw_sim: float = 0.0
    genre_ratio: float | None = None
    chance: float = 0.0
    pred_views: float | None = None
    exp_views: float = 0.0
    reach: float = 1.0
    contrib: dict = field(default_factory=dict)
    match_sources: Counter = field(default_factory=Counter)  # title / tags / description / steam link
    median_all: float | None = None
    stage: str = "found"          # found -> filtered_size / not_analysed / inactive / ranked
    added_manually: bool = False

    @property
    def url(self) -> str:
        if self.custom_url:
            h = self.custom_url if self.custom_url.startswith("@") else "@" + self.custom_url
            return f"https://www.youtube.com/{h}"
        return f"https://www.youtube.com/channel/{self.channel_id}"

    @property
    def days_since_upload(self) -> int | None:
        if not self.last_upload:
            return None
        return (datetime.now(timezone.utc) - self.last_upload).days


class Cancelled(Exception):
    pass


class InputError(ValueError):
    """A problem with what the user typed (shown as is)."""


# ---------------------------------------------------------------- text utils
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _game_variants(game: str) -> list[str]:
    """'SteamWorld Dig' also matches 'SteamWorld Dig 2' / 'Steamworld-Dig II'; a trailing
    sequel number in the list entry is optional ('Hydroneer 2' still matches 'Hydroneer')."""
    g = norm(game)
    base = re.sub(r"\s+(?:\d+|ii|iii|iv)$", "", g)
    return [v for v in {g, base} if v]


NAME_FILLER = {"a", "an", "the", "of", "about", "game", "to", "and", "in", "on", "for", "with"}


def _significant(game: str) -> list[str]:
    return [w for w in norm(game).split() if w not in NAME_FILLER and not w.isdigit()]


def mentions(text: str, game: str) -> bool:
    """Exact name (with/without sequel number or spaces), or — for names with 2+ meaningful
    words — all of those words in the title: 'Digging a Hole' matches 'A Game About Digging A Hole'."""
    t = f" {norm(text)} "
    if any(f" {v} " in t or f" {v.replace(' ', '')} " in t for v in _game_variants(game)):
        return True
    sig = _significant(game)
    return len(sig) >= 2 and all(f" {w} " in t for w in sig)


def match_video(title: str, tags: list[str], description: str, game: str, appid=None) -> str | None:
    """Where a video shows it is about `game`: 'title', 'tags', 'steam link' or 'description'."""
    if mentions(title, game):
        return "title"
    if any(mentions(t, game) for t in tags or ()):
        return "tags"
    if appid and re.search(rf"store\.steampowered\.com/app/{int(appid)}\b", description or ""):
        return "steam link"
    d = f" {norm(description)} "
    if len(norm(game)) >= 5 and any(f" {v} " in d for v in _game_variants(game)):
        return "description"
    return None


def strip_games(text: str, games: list[str]) -> str:
    """Remove game names so content similarity measures the genre, not the names
    (game names are counted separately as 'similar games played')."""
    t = f" {norm(text)} "
    for g in games:
        for v in _game_variants(g):
            t = t.replace(f" {v} ", " ")
    return t


def tokens(text: str) -> list[str]:
    toks = [t.strip("'-") for t in TOKEN_RE.findall((text or "").lower())]
    toks = [t for t in toks if len(t) > 2 and t not in STOPWORDS and not t.isdigit()]
    return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


def tfidf_cosines(query: str, docs: list[str]) -> list[float]:
    tok_docs = [Counter(tokens(d)) for d in docs]
    q = Counter(tokens(query))
    n = len(docs) + 1
    df = Counter()
    for c in tok_docs + [q]:
        df.update(c.keys())
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 1.0 for t in df}

    def vec(c):
        v = {t: (1 + math.log(f)) * idf[t] for t, f in c.items()}
        nrm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / nrm for t, x in v.items()}

    qv = vec(q)
    return [sum(w * qv.get(t, 0.0) for t, w in vec(c).items()) for c in tok_docs]


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------- score pieces
def size_score(subs: int | None) -> float:
    """Indie sweet spot ~3k-100k subscribers."""
    if subs is None:
        return 0.5
    if subs <= 0:
        return 0.0
    x = math.log10(subs)
    lo0, lo1, hi1, hi0 = 2.0, math.log10(3_000), 5.0, math.log10(5_000_000)
    if x <= lo0 or x >= hi0:
        return 0.05
    if x < lo1:
        return (x - lo0) / (lo1 - lo0)
    if x <= hi1:
        return 1.0
    return max(0.05, 1 - (x - hi1) / (hi0 - hi1))


def engagement_score(median_views, subs) -> float:
    if median_views is None:
        return 0.3
    if not subs:
        return 0.5
    return min(1.0, (median_views / subs) / 0.25)


def activity_score(days: int | None) -> float:
    if days is None:
        return 0.0
    if days <= 14:
        return 1.0
    return max(0.0, 1 - (days - 14) / 106)


def reachability(subs: int | None) -> float:
    """Share of pitches a channel of this size can realistically act on. A 500k creator
    says he gets 30-100 pitches a day and can cover only ~10-25% of games that fit him,
    so this is calibrated to ≈0.23 at 500k: 25k -> 0.86, 150k -> 0.5, 1M -> 0.13, 5M -> 0.03."""
    if not subs:
        return 0.8
    return 1 / (1 + subs / 150_000)


def coverage_score(n: int) -> float:
    return 1 - 0.5 ** n if n > 0 else 0.0


def fmt_num(n) -> str:
    if n is None:
        return "hidden"
    n = float(n)
    for div, suf in ((1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(int(n))


# ------------------------------------------------------------------ pipeline
STAGE_TEXT = {
    "filtered_size": "outside your subscriber range",
    "not_analysed": "found, but not among the channels analysed (raise 'Channels analysed')",
    "inactive": "no upload within your 'Max inactive' days",
    "ranked": "in the results",
}


def fill_channel(c: Candidate, item: dict) -> None:
    sn, st = item.get("snippet", {}), item.get("statistics", {})
    c.title = sn.get("title", c.title)
    c.custom_url = sn.get("customUrl", "")
    c.description = sn.get("description", "")
    c.country = sn.get("country", "")
    c.keywords_text = item.get("brandingSettings", {}).get("channel", {}).get("keywords", "")
    c.subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))
    c.total_views = int(st.get("viewCount", 0))
    c.video_count = int(st.get("videoCount", 0))
    c.uploads_pid = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")


def analyse(c: Candidate, client, games: list[str], n_videos: int = 100, appids: dict | None = None) -> None:
    """Latest uploads + their views and tags -> all per-channel signals.
    Cost: 2 units per 50 videos. Raises QuotaExceeded."""
    appids = appids or {}
    c.recent = client.uploads(c.uploads_pid, n_videos)
    fresh = [v for v in c.recent if (_age_days(v["published"]) or 0) >= 3]
    hits = [vid for ids in c.hit_videos.values() for vid in ids]
    details = client.video_details([v["video_id"] for v in c.recent] + hits)
    views = {k: d["views"] for k, d in details.items() if d.get("views") is not None}
    dates = [d for d in (parse_time(v["published"]) for v in c.recent) if d]
    c.last_upload = max(dates) if dates else None
    vals = [views[v["video_id"]] for v in fresh[:15] if v["video_id"] in views]
    c.median_views = statistics.median(vals) if vals else None
    every = [views[v["video_id"]] for v in fresh if v["video_id"] in views]
    c.median_all = statistics.median(every) if every else None
    game_vids = {g: set(ids) for g, ids in c.hit_videos.items()}
    c.key_videos = c.sponsor_videos = 0
    c.match_sources = Counter()
    for v in c.recent:
        tags = details.get(v["video_id"], {}).get("tags", [])
        for g in games:
            src = match_video(v["title"], tags, v["description"], g, appids.get(g))
            if src:
                c.matched_games.add(g)
                game_vids.setdefault(g, set()).add(v["video_id"])
                c.match_sources[src] += 1
        text = f"{v['title']}\n{v['description']}"
        c.key_videos += bool(KEY_RE.search(text))
        c.sponsor_videos += bool(SPONSOR_RE.search(text))
    c.game_views = {g: [views[i] for i in ids if i in views] for g, ids in game_vids.items()}
    c.game_views = {g: vs for g, vs in c.game_views.items() if vs}
    allv = [x for vs in c.game_views.values() for x in vs]
    base = c.median_all or c.median_views   # same time window as the game videos
    c.genre_ratio = statistics.median(allv) / max(1.0, base) if allv and base else None
    c.emails = youtube_emails(c)
    c.links = own_links(c.description, [v["description"] for v in c.recent])


def game_document(settings: Settings) -> str:
    kws = [k for k in settings.keywords if k.strip()]
    return " ".join([settings.game_name, settings.game_description, " ".join(settings.tags), " ".join(kws)])


def score_all(active: list[Candidate], settings: Settings, progress=lambda m, f=None: None,
              embedder_loader=None, check=lambda: None):
    """Content similarity, match score, learned coverage model, predictions. -> (model, semantic status)"""
    games = [g.strip() for g in settings.comparable_games if g.strip()]
    progress("Calculating scores…", 0.96)
    game_doc = game_document(settings)
    docs = [
        strip_games(" ".join([c.title, c.description, c.keywords_text]
                             + [v["title"] for v in c.recent] * 2), games + [settings.game_name])
        for c in active
    ]
    raw = tfidf_cosines(game_doc, docs) if active else []
    top_raw = max(raw, default=0) or 1.0
    for c, r in zip(active, raw):
        c.kw_sim = r / top_raw

    semantic = "off"
    if settings.use_semantic and active and embedder_loader is not None:
        check()
        progress("Loading the semantic model…", 0.97)
        emb, semantic = embedder_loader(progress)
        if emb is not None:
            progress("Semantic matching…", 0.98)
            try:
                sims = emb.similarities(game_doc, docs)
                lo, hi = min(sims), max(sims)
                for c, v in zip(active, sims):
                    c.sem_sim = (v - lo) / (hi - lo) if hi > lo else 0.0
            except Exception as e:
                semantic = f"semantic model failed ({type(e).__name__}) — using keywords only"

    for c in active:
        content = 0.5 * c.kw_sim + 0.5 * c.sem_sim if semantic.startswith("semantic model on") else c.kw_sim
        sc = {
            "content": content,
            "coverage": coverage_score(len(c.matched_games)),
            "size": size_score(c.subs),
            "engagement": engagement_score(c.median_views, c.subs),
            "activity": activity_score(c.days_since_upload),
        }
        c.scores = sc
        base = sum(WEIGHTS[k] * v for k, v in sc.items())
        relevance = max(sc["content"], sc["coverage"])
        c.total = round(100 * base * (0.35 + 0.65 * min(1.0, relevance / 0.5)), 1)

    progress("Learning what predicts coverage in this genre…", 0.99)
    mdl = cov_model.train([c for c in active if not c.added_manually], games)
    for c in active:
        c.chance, c.contrib = mdl.predict(c)
        # the model ranks channels *within* the pool it found; a channel with no genre
        # evidence at all (no similar games, off-topic content) must not ride on its size
        relevance = max(c.scores["content"], c.scores["coverage"])
        c.chance *= max(0.02, min(1.0, relevance / 0.5)) ** 1.5
        c.pred_views = None
        if c.median_views is not None:
            perf = min(3.0, max(0.3, c.genre_ratio)) if c.genre_ratio else 1.0
            c.pred_views = c.median_views * perf
        c.reach = reachability(c.subs)
        c.exp_views = c.chance * (c.pred_views or 0) * c.reach
        c.reasons = reasons(c)
    active.sort(key=lambda c: (c.exp_views, c.chance), reverse=True)
    return mdl, semantic


def run(settings: Settings, client, progress=lambda msg, frac=None: None, cancel=None,
        embedder_loader=None):
    """Returns (ranked_candidates, stats_dict). Raises ApiError; on quota
    exhaustion it ranks whatever was collected so far."""

    def check():
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    games = [g.strip() for g in settings.comparable_games if g.strip()]
    kws = [k.strip() for k in settings.keywords if k.strip()]
    if not games and not kws:
        raise InputError("Add at least one comparable game or keyword.")

    cands: dict[str, Candidate] = {}
    excluded: set[str] = set()
    quota_hit = False

    # 1) discovery ---------------------------------------------------------
    queries = [(g, f"{g} gameplay") for g in games] + [(None, k) for k in kws]
    for i, (game, q) in enumerate(queries):
        check()
        progress(f"Searching: {q}", 0.4 * i / len(queries))
        try:
            hits = client.search_videos(q, settings.pages_per_query, settings.language)
        except QuotaExceeded:
            quota_hit = True
            progress("⚠ Daily YouTube quota used up — continuing with what was found.")
            break
        for h in hits:
            cid = h["channel_id"]
            if not cid:
                continue
            if cid in settings.exclude_ids:
                excluded.add(cid)
                continue
            c = cands.setdefault(cid, Candidate(channel_id=cid, title=h["channel_title"]))
            c.found_via[q] += 1
            if game and mentions(h["title"], game):
                c.matched_games.add(game)
                if h.get("video_id"):
                    c.hit_videos.setdefault(game, set()).add(h["video_id"])
    progress(f"Found {len(cands)} unique channels.", 0.4)

    # 2) channel stats (1 unit / 50 channels) -------------------------------
    # search quota (100/unit) may be gone while cheap calls still fit -> try anyway
    check()
    if cands:
        try:
            for item in client.channels(list(cands)):
                c = cands.get(item["id"])
                if c:
                    fill_channel(c, item)
        except QuotaExceeded:
            quota_hit = True

    pool = []
    for c in cands.values():
        if c.subs is None or settings.min_subs <= c.subs <= settings.max_subs:
            pool.append(c)
        else:
            c.stage = "filtered_size"
    # cheap pre-rank decides who gets the (still cheap) deep look
    pool.sort(
        key=lambda c: (len(c.matched_games), sum(c.found_via.values()), size_score(c.subs)),
        reverse=True,
    )
    deep = pool[: settings.max_deep]
    for c in pool[settings.max_deep:]:
        c.stage = "not_analysed"

    # 3) recent uploads + views (2 units / channel) --------------------------
    for i, c in enumerate(deep):
        check()
        if i % 5 == 0:
            progress(f"Analysing channels ({i}/{len(deep)})…", 0.45 + 0.5 * i / max(1, len(deep)))
        try:
            analyse(c, client, games, settings.videos_per_channel, settings.game_appids)
        except QuotaExceeded:
            quota_hit = True
            progress("⚠ Daily YouTube quota used up — ranking what was analysed.")
            break

    analysed = [c for c in deep if c.recent]
    active = []
    for c in deep:
        if not c.recent:
            c.stage = "not_analysed"
        elif c.days_since_upload is not None and c.days_since_upload <= settings.max_inactive_days:
            c.stage = "ranked"
            active.append(c)
        else:
            c.stage = "inactive"

    # 4) signals + prediction -------------------------------------------------
    mdl, semantic = score_all(active, settings, progress, embedder_loader, check)
    stats = {
        "found": len(cands),
        "in_size_range": len(pool),
        "analysed": len(analysed),
        "ranked": len(active),
        "units": client.units_used,
        "cache_hits": client.cache_hits,
        "quota_hit": quota_hit,
        "semantic": semantic,
        "model": mdl,
        "all": cands,
        "excluded": excluded,
        "queries": [q for _, q in queries],
    }
    progress("Done.", 1.0)
    return active, stats


def add_channel(channel_id: str, client, settings: Settings, active: list[Candidate],
                all_cands: dict, embedder_loader=None) -> Candidate:
    """Analyse one channel on demand (≈3 units), add it to the ranked list and re-rank."""
    games = [g.strip() for g in settings.comparable_games if g.strip()]
    c = all_cands.get(channel_id) or Candidate(channel_id=channel_id)
    if not c.uploads_pid:
        items = client.channels([channel_id])
        if not items:
            raise InputError("YouTube doesn't know that channel.")
        fill_channel(c, items[0])
    analyse(c, client, games, settings.videos_per_channel, settings.game_appids)
    c.added_manually = c.stage != "ranked"
    c.stage = "ranked"
    all_cands[channel_id] = c
    if c not in active:
        active.append(c)
    score_all(active, settings, embedder_loader=embedder_loader)
    return c


def explain(c: Candidate | None, settings: Settings, ref_title: str = "", subs=None,
            excluded: bool = False, queries=()) -> str:
    """Plain-language reason a channel is (not) in the results."""
    if excluded:
        return "skipped because it's marked as contacted ('Skip creators already contacted' is on)"
    if c is None:
        q = ", ".join(f"“{q}”" for q in list(queries)[:3]) + ("…" if len(queries) > 3 else "")
        return (f"none of its videos were in the top results of your searches ({q}). YouTube search "
                "returns ~50 videos per query, so big but less-relevant channels are often missed. "
                "Use “Add to results” to analyse it anyway, add a comparable game they played, "
                "or raise 'Pages / search'.")
    if c.stage == "filtered_size":
        return (f"filtered out: {fmt_num(c.subs)} subscribers is outside your range "
                f"({fmt_num(settings.min_subs)}–{fmt_num(settings.max_subs)})")
    if c.stage == "inactive":
        return f"filtered out: last upload {c.days_since_upload} days ago (your limit: {settings.max_inactive_days})"
    return STAGE_TEXT.get(c.stage, c.stage)


def youtube_emails(c: Candidate) -> list[dict]:
    """Emails from the channel description (trusted) and from video descriptions (only when
    they repeat or match the channel — a one-off address in one video is usually a sponsor's)."""
    handle = (c.custom_url or "").lstrip("@")
    out: dict[str, dict] = {}
    for e in extract_emails([c.description]):
        conf, why = rate_email(e["email"], c.title, handle, in_channel_description=True, business=e["business"])
        if conf:
            out[e["email"]] = {**e, "source": "YouTube", "url": c.url, "conf": conf, "why": why}
    seen: dict[str, int] = {}
    biz: dict[str, bool] = {}
    for v in c.recent:
        for e in extract_emails([v["description"]]):
            seen[e["email"]] = seen.get(e["email"], 0) + 1
            biz[e["email"]] = biz.get(e["email"], False) or e["business"]
    for email, n in seen.items():
        if email in out:
            continue
        conf, why = rate_email(email, c.title, handle, business=biz[email])
        if conf is None or (conf == "low" and n < 2):
            continue
        if n >= 3 and conf == "medium":
            conf, why = "high", f"in {n} of their video descriptions (their standard footer)"
        out[email] = {"email": email, "business": biz[email], "source": "YouTube",
                      "url": c.url, "conf": conf, "why": why}
    return sorted(out.values(), key=lambda e: (CONF_ORDER.get(e["conf"], 9), not e["business"]))


def _age_days(published: str | None) -> int | None:
    d = parse_time(published)
    return (datetime.now(timezone.utc) - d).days if d else None


def reasons(c: Candidate) -> list[str]:
    s, out = c.scores, []
    if c.pred_views:
        out.append(f"Coverage chance {100 * c.chance:.0f}% · ~{fmt_num(c.pred_views)} predicted views · "
                   f"~{fmt_num(c.exp_views)} expected views per pitch")
    else:
        out.append(f"Coverage chance {100 * c.chance:.0f}%")
    if c.key_videos:
        out.append(f"Has accepted developer keys before ({c.key_videos} recent video"
                   f"{'s' if c.key_videos > 1 else ''} mention a provided key/copy)")
    if c.genre_ratio:
        out.append(f"Their videos on similar games get {c.genre_ratio:.1f}× their usual views"
                   + (" — the audience likes this genre" if c.genre_ratio >= 1.2
                      else " — the genre underperforms on this channel" if c.genre_ratio < 0.7 else ""))
    if c.matched_games:
        where = ", ".join(f"{k} {n}" for k, n in c.match_sources.most_common())
        out.append("Played similar games: " + ", ".join(sorted(c.matched_games))
                   + (f" (found in: {where})" if where else ""))
    else:
        out.append(f"No similar games in their last {len(c.recent)} videos (titles, tags, descriptions)")
    out.append(
        "Content is very close to your game" if s["content"] >= 0.6
        else "Content is partly related to your game" if s["content"] >= 0.3
        else "Content is fairly far from your game"
    )
    size_txt = fmt_num(c.subs) + " subscribers"
    out.append(
        f"{size_txt} — ideal size for an indie game" if s["size"] >= 0.9
        else f"{size_txt} — small, but likely to reply" if (c.subs or 0) < 3000
        else f"{size_txt} — big, less likely to reply"
    )
    if c.median_views is not None and c.subs:
        out.append(f"Median views per video: {fmt_num(c.median_views)} ({100 * c.median_views / c.subs:.0f}% of subscribers)")
    d = c.days_since_upload
    if d is not None:
        out.append("Active: posted in the last 2 weeks" if d <= 14 else f"Last upload {d} days ago")
    if c.emails:
        e = c.emails[0]
        src = e.get("source", "YouTube")
        where = {"YouTube": "Public email in their YouTube descriptions",
                 "manual": "Email added by you (from the YouTube About tab)",
                 "browser": "Email found on their website (via browser)"}.get(src, f"Email found on their {src}")
        conf = e.get("conf", "")
        tail = " — verified by you" if conf == "verified" else (f" — {conf} confidence: {e.get('why', '')}" if conf else "")
        out.append(where + tail)
    else:
        out.append("No public email found — use Reveal email (YouTube About tab) or their socials")
    return out


# --------------------------------------------------------------- outreach
DEFAULT_TEMPLATE = """Subject: {game} — a {hook} game you might enjoy (free key)

Hi {creator},

I really enjoyed your {matched} videos, so I thought {game} might be right up your alley.

{pitch}

I'd love to send you a free Steam key — no strings attached, play it only if you like it.

Trailer: [TRAILER LINK]
Steam page: [STEAM LINK]

Thanks for your time!
[YOUR NAME]
"""


def draft_email(c: Candidate, settings_like: dict, template: str = DEFAULT_TEMPLATE) -> str:
    games = sorted(c.matched_games)
    matched = games[0] if games else "recent"
    desc = (settings_like.get("game_description") or "").strip()
    first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0] if desc else ""
    values = {
        "creator": c.title,
        "game": settings_like.get("game_name") or "our game",
        "matched": matched,
        "matched_games": ", ".join(games) or "your recent",
        "pitch": desc or "[SHORT PITCH]",
        "hook": (settings_like.get("hook") or "").strip() or "digging",
        "first_sentence": first,
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError) as e:
        return f"[Template error: {e}. Available fields: {', '.join('{'+k+'}' for k in values)}]"

"""Self-calibrating coverage model.

Question it answers: "how likely is this channel to cover a game like yours?"

Labels come from YouTube itself, so no outreach history is needed: for every
comparable game we already know which analysed channels covered it. The model
hides one game at a time (leave-one-game-out), computes each channel's signals
WITHOUT that game, and learns which signals predict that the channel covered it.
Fitted fresh on every search, for the exact genre you described.

Logistic regression with L2 regularisation on standardised features,
plain Python (≈150 channels × ~8 games — tiny). When there is too little data
it falls back to sensible default weights and says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

FEATURES = ["semantic", "keywords", "similar_games", "genre_performance",
            "accepts_keys", "sponsored", "size", "engagement", "activity"]
LABELS = {
    "semantic": "Content meaning (semantic)",
    "keywords": "Content keywords",
    "similar_games": "Similar games played",
    "genre_performance": "Views on similar games",
    "accepts_keys": "Accepted dev keys before",
    "sponsored": "Does sponsored videos",
    "size": "Channel size",
    "engagement": "Engagement",
    "activity": "Recent activity",
}
# Used only when there is not enough data to learn (standardised scale).
PRIOR = {"semantic": 0.6, "keywords": 0.4, "similar_games": 1.0, "genre_performance": 0.4,
         "accepts_keys": 0.5, "sponsored": 0.2, "size": -0.3, "engagement": 0.3, "activity": 0.3}
PRIOR_INTERCEPT = -1.0
MIN_POS = 3
MIN_NEG = 3
MIN_ROWS = 40


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def features(c, exclude: str | None = None) -> dict:
    """Signals for one channel. `exclude` = a game whose evidence must be ignored."""
    games = {g for g in c.matched_games if g != exclude}
    views = [v for g, vs in c.game_views.items() if g != exclude for v in vs]
    perf = 0.0
    if views and c.median_views:
        ratio = sorted(views)[len(views) // 2] / max(1.0, c.median_views)
        perf = math.log(min(5.0, max(0.2, ratio)))
    subs = c.subs if c.subs else 20_000
    eng = 0.0
    if c.median_views is not None and c.subs:
        eng = math.log(min(2.0, max(0.005, c.median_views / c.subs)))
    d = c.days_since_upload
    act = 0.0 if d is None else (1.0 if d <= 14 else max(0.0, 1 - (d - 14) / 106))
    return {
        "semantic": c.sem_sim,
        "keywords": c.kw_sim,
        "similar_games": math.log1p(len(games)),
        "genre_performance": perf,
        "accepts_keys": 1.0 if c.key_videos else 0.0,
        "sponsored": 1.0 if c.sponsor_videos else 0.0,
        "size": math.log10(max(10, subs)),
        "engagement": eng,
        "activity": act,
    }


@dataclass
class Model:
    coef: dict
    intercept: float
    mean: dict
    std: dict
    learned: bool
    rows: int = 0
    positives: int = 0
    games: list = field(default_factory=list)
    active: list = field(default_factory=lambda: list(FEATURES))

    def z(self, x: dict) -> dict:
        return {f: (x[f] - self.mean[f]) / self.std[f] if f in self.active else 0.0 for f in FEATURES}

    def predict(self, c) -> tuple[float, dict]:
        """(probability, contribution of each signal in log-odds)."""
        zx = self.z(features(c))
        contrib = {f: self.coef.get(f, 0.0) * zx[f] for f in FEATURES}
        return sigmoid(self.intercept + sum(contrib.values())), contrib

    def summary(self, n: int = 3) -> list[tuple[str, float]]:
        top = sorted(((f, w) for f, w in self.coef.items() if f in self.active), key=lambda t: -abs(t[1]))
        return [(LABELS[f], w) for f, w in top[:n]]


def _standardise(rows: list[dict]):
    mean, std, active = {}, {}, []
    for f in FEATURES:
        vals = [r[f] for r in rows]
        m = sum(vals) / len(vals)
        s = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))
        mean[f], std[f] = m, (s if s > 1e-9 else 1.0)
        if s > 1e-9:
            active.append(f)
    return mean, std, active


def _fit(X: list[list[float]], y: list[int], l2: float = 1.0, iters: int = 400, lr: float = 0.5):
    """Batch gradient descent for L2-regularised logistic regression."""
    n, k = len(X), len(X[0])
    w, b = [0.0] * k, math.log((sum(y) + 0.5) / (n - sum(y) + 0.5))
    for _ in range(iters):
        gw, gb = [0.0] * k, 0.0
        for xi, yi in zip(X, y):
            err = sigmoid(b + sum(a * c for a, c in zip(w, xi))) - yi
            gb += err
            for j in range(k):
                gw[j] += err * xi[j]
        b -= lr * gb / n
        w = [wj - lr * (gwj / n + l2 * wj / n) for wj, gwj in zip(w, gw)]
    return w, b


def found_without(c, game: str, query_for) -> bool:
    """Would this channel be in the pool even if we had never searched for `game`?
    Every channel was discovered THROUGH some game; when that game is the one hidden,
    the channel looks like 'covered it with no other evidence' — a pure artefact of how
    it was found. Training only on channels found through other searches removes that bias."""
    if not c.found_via:            # no discovery info (e.g. tests) -> nothing to correct
        return True
    return any(q != query_for(game) for q in c.found_via)


def train(cands, games: list[str], query_for=lambda g: f"{g} gameplay") -> Model:
    rows, ys, used = [], [], []
    for g in games:
        pool = [c for c in cands if found_without(c, g, query_for)]
        y = [1 if g in c.matched_games else 0 for c in pool]
        if sum(y) < MIN_POS or len(y) - sum(y) < MIN_NEG:
            continue
        used.append(g)
        rows += [features(c, exclude=g) for c in pool]
        ys += y

    if len(rows) < MIN_ROWS or len(used) < 2:
        base = [features(c) for c in cands] or [dict.fromkeys(FEATURES, 0.0)]
        mean, std, active = _standardise(base)
        return Model(dict(PRIOR), PRIOR_INTERCEPT, mean, std, learned=False, games=used, active=active)

    mean, std, active = _standardise(rows)
    X = [[(r[f] - mean[f]) / std[f] for f in active] for r in rows]
    w, b = _fit(X, ys)
    coef = dict.fromkeys(FEATURES, 0.0)
    coef.update(zip(active, w))
    return Model(coef, b, mean, std, learned=True, rows=len(rows), positives=sum(ys),
                 games=used, active=active)

"""Synthetic world with a known truth: does the self-calibrating model find it?"""
import random, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import model
from scout import Candidate

GAMES = ["Hydroneer", "SteamWorld Dig", "Dome Keeper", "Deep Rock Galactic", "Mining Mechanics", "A Game About Digging A Hole"]

def world(seed, n=150, flipped=False):
    """flipped=True: a genre where my default intuitions are WRONG — big channels cover
    more, keys don't matter, and semantic text is a weak signal."""
    rnd = random.Random(seed); out = []
    for i in range(n):
        love = rnd.random() < 0.35                      # hidden truth: audience loves the genre
        keys = rnd.random() < (0.45 if love else 0.12)  # accepts dev keys
        subs = int(10 ** rnd.uniform(3, 6.3))
        c = Candidate(channel_id=f"c{i}", title=f"c{i}", subs=subs)
        c.median_views = subs * rnd.uniform(0.03, 0.4)
        c.last_upload = datetime.now(timezone.utc) - timedelta(days=rnd.choice([1, 5, 10, 30, 60]))
        c.sem_sim = min(1, max(0, (0.65 if love else 0.25) + rnd.gauss(0, 0.18)))
        c.kw_sim = min(1, max(0, (0.5 if love else 0.2) + rnd.gauss(0, 0.2)))
        c.key_videos = int(keys); c.sponsor_videos = int(rnd.random() < 0.2)
        size_pen = 1.0 if subs < 300_000 else 0.5
        if flipped:
            size_pen = 0.4 if subs < 30_000 else 1.3
            c.sem_sim = min(1, max(0, 0.4 + rnd.gauss(0, 0.25)))   # semantic says nothing here
        def p_cover():
            if flipped:
                return min(0.95, (0.5 if love else 0.05) * size_pen)
            return (0.42 if love else 0.04) * (1.4 if keys else 1.0) * size_pen
        for g in GAMES:
            if rnd.random() < p_cover():
                c.matched_games.add(g)
                c.game_views[g] = [c.median_views * (rnd.uniform(1.2, 2.5) if love else rnd.uniform(0.4, 1.0))]
        c.truth_new = rnd.random() < p_cover()          # would they cover a NEW game (his game)?
        c.truth_p = p_cover()
        out.append(c)
    return out

def auc(scores, labels):
    pos = [s for s, l in zip(scores, labels) if l]; neg = [s for s, l in zip(scores, labels) if not l]
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))

def main():
    aucs_learned, aucs_prior, aucs_count = [], [], []
    calib = []
    for seed in range(5):
        cs = world(seed)
        t = time.time(); m = model.train(cs, GAMES); dt = time.time() - t
        assert m.learned, "should learn with 150 channels x 6 games"
        preds = [m.predict(c)[0] for c in cs]
        truth = [c.truth_p for c in cs]   # true probability of covering a new game
        aucs_learned.append(auc(preds, [c.truth_new for c in cs]))
        prior = model.Model(dict(model.PRIOR), model.PRIOR_INTERCEPT, m.mean, m.std, learned=False, active=m.active)
        aucs_prior.append(auc([prior.predict(c)[0] for c in cs], [c.truth_new for c in cs]))
        aucs_count.append(auc([len(c.matched_games) for c in cs], [c.truth_new for c in cs]))
        calib += list(zip(preds, truth))
        if seed == 0:
            print(f"trained on {m.rows} rows ({m.positives} positives, games: {len(m.games)}) in {dt:.2f}s")
            print("learned coefficients:", {k: round(v, 2) for k, v in sorted(m.coef.items(), key=lambda t: -abs(t[1]))})
    print(f"AUC predicting who covers a NEW game — learned model: {sum(aucs_learned)/5:.3f} | "
          f"default weights: {sum(aucs_prior)/5:.3f} | just counting similar games: {sum(aucs_count)/5:.3f}")
    # calibration: predicted vs true probability, by bins
    calib.sort()
    k = len(calib) // 5
    print("calibration (mean predicted → mean true probability):")
    for b in range(5):
        chunk = calib[b * k:(b + 1) * k]
        print(f"  {sum(p for p, _ in chunk)/len(chunk):.2f} → {sum(t for _, t in chunk)/len(chunk):.2f}")
    fl, fp = [], []
    for seed in range(5):
        cs = world(100 + seed, flipped=True)
        m = model.train(cs, GAMES)
        prior = model.Model(dict(model.PRIOR), model.PRIOR_INTERCEPT, m.mean, m.std, learned=False, active=m.active)
        fl.append(auc([m.predict(c)[0] for c in cs], [c.truth_new for c in cs]))
        fp.append(auc([prior.predict(c)[0] for c in cs], [c.truth_new for c in cs]))
    print(f"Genre where default intuitions are wrong — learned model: {sum(fl)/5:.3f} | default weights: {sum(fp)/5:.3f}")
    print("  learned there:", {k: round(v, 2) for k, v in sorted(m.coef.items(), key=lambda t: -abs(t[1]))[:4]})
    assert sum(fl) / 5 > sum(fp) / 5
    m0 = model.train(world(0), GAMES)
    assert m0.coef["similar_games"] > 0 and m0.coef["semantic"] > 0 and m0.coef["accepts_keys"] > 0
    assert sum(aucs_learned) / 5 > 0.75
    # too little data -> default weights, flagged
    small = world(1, n=8)
    assert not model.train(small, GAMES).learned
    print("MODEL TESTS PASSED")

main()

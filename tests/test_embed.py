"""Embedding wiring: tokenizer -> onnx session -> mean pooling -> cosine (model simulated)."""
import sys, tempfile, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["APPDATA"] = tempfile.mkdtemp()
import numpy as np
import embed

VOCAB = {w: i + 1 for i, w in enumerate("dig digging mine mining ore drill cave barbie dress makeup doll gold".split())}
class Enc:
    def __init__(s, ids, n): s.ids = ids + [0] * (n - len(ids)); s.attention_mask = [1] * len(ids) + [0] * (n - len(ids))
class Tok:
    def encode_batch(s, texts):
        toks = [[VOCAB.get(w, 99) for w in t.lower().split()][:8] or [99] for t in texts]
        n = max(map(len, toks)); return [Enc(t, n) for t in toks]
class Inp:
    def __init__(s, name): s.name = name
class Sess:   # "model": digging words -> axis 0, barbie words -> axis 1, unknown -> axis 2
    def get_inputs(s): return [Inp("input_ids"), Inp("attention_mask"), Inp("token_type_ids")]
    def run(s, _, feed):
        ids = feed["input_ids"]; assert set(feed) == {"input_ids", "attention_mask", "token_type_ids"}
        h = np.zeros(ids.shape + (3,), dtype=np.float32)
        h[..., 0] = np.isin(ids, [1, 2, 3, 4, 5, 6, 7, 12]); h[..., 1] = np.isin(ids, [8, 9, 10, 11]); h[..., 2] = ids == 99
        h[ids == 0] = 50.0   # padding garbage must be ignored by the mask
        return [h]

e = embed.Embedder(Sess(), Tok())
sims = e.similarities("digging mining ore", ["cave drill gold", "barbie dress makeup doll", "drill barbie", "x y z"])
print([round(x, 3) for x in sims])
assert sims[0] > 0.99 and sims[1] < 0.01 and 0.5 < sims[2] < 0.9 and sims[3] < 0.01
v = e.encode(["dig", "dig dig dig dig dig dig"])
assert abs(float(np.linalg.norm(v[0])) - 1) < 1e-5 and np.allclose(v[0], v[1])   # padding ignored, normalised
emb, status = embed.load_embedder()
print("loading the real model:", status)
if emb is None:
    # no libraries / no internet: the app must fall back cleanly, with a reason
    assert status.startswith("semantic model unavailable"), status
else:
    # the real pretrained MiniLM is available (e.g. on CI): check it understands meaning
    q = "a digging simulator: mine ore, drill underground, upgrade your shovel"
    sims = emb.similarities(q, ["underground excavation game with mining and gems",
                                "barbie dress up and makeup challenge",
                                "cooking recipes for beginners"])
    print("real MiniLM similarities:", [round(x, 3) for x in sims])
    assert sims[0] > sims[1] and sims[0] > sims[2], sims
print("EMBED TESTS PASSED")

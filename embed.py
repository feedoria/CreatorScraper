"""Semantic similarity with a pretrained sentence-embedding model.

Model: all-MiniLM-L6-v2 (sentence-transformers), int8-quantized ONNX export
(~23 MB), run with onnxruntime. Downloaded once from Hugging Face into the
app's data folder. Nothing is trained here; the model is used as-is.

If anything is missing (no internet on first run, library not installed),
`load_embedder` returns None and the app falls back to TF-IDF only.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from storage import data_dir

MODEL_NAME = "all-MiniLM-L6-v2"
BASE_URL = "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/"
FILES = {"model.onnx": "onnx/model_quantized.onnx", "tokenizer.json": "tokenizer.json"}
MAX_TOKENS = 256
BATCH = 16


def model_dir() -> Path:
    return data_dir() / "models" / MODEL_NAME


def is_downloaded() -> bool:
    d = model_dir()
    return all((d / f).exists() and (d / f).stat().st_size > 0 for f in FILES)


def download(progress=lambda msg, frac=None: None) -> None:
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    for local, remote in FILES.items():
        target = d / local
        if target.exists() and target.stat().st_size > 0:
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        with requests.get(BASE_URL + remote, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0)) or None
            got = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
                    got += len(chunk)
                    if total:
                        progress(f"Downloading semantic model ({got / 1e6:.0f}/{total / 1e6:.0f} MB, one time only)…",
                                 got / total)
        os.replace(tmp, target)


class Embedder:
    """encode(texts) -> list of L2-normalised vectors (numpy arrays)."""

    def __init__(self, session, tokenizer):
        import numpy as np
        self.np = np
        self.session = session
        self.tok = tokenizer
        self.input_names = {i.name for i in session.get_inputs()}

    def encode(self, texts: list[str]):
        np = self.np
        out = []
        for i in range(0, len(texts), BATCH):
            enc = self.tok.encode_batch([t or " " for t in texts[i:i + BATCH]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.input_names})[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)  # mean pooling
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
            out.extend(pooled)
        return out

    def similarities(self, query: str, docs: list[str]) -> list[float]:
        vecs = self.encode([query] + docs)
        q = vecs[0]
        return [float(self.np.dot(q, v)) for v in vecs[1:]]


def load_embedder(progress=lambda msg, frac=None: None) -> tuple[Embedder | None, str]:
    """Returns (embedder or None, status text)."""
    try:
        import numpy  # noqa: F401
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as e:
        return None, f"semantic model unavailable (missing library: {e.name})"
    try:
        if not is_downloaded():
            download(progress)
        d = model_dir()
        tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        tok.enable_truncation(max_length=MAX_TOKENS)
        tok.enable_padding(pad_id=0, pad_token="[PAD]")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        sess = ort.InferenceSession(str(d / "model.onnx"), sess_options=opts,
                                    providers=["CPUExecutionProvider"])
        return Embedder(sess, tok), "semantic model on (MiniLM)"
    except requests.RequestException:
        return None, "semantic model unavailable (could not download it — check internet)"
    except Exception as e:  # corrupt/partial file, incompatible runtime, …
        for f in FILES:  # force a clean re-download next time
            try:
                (model_dir() / f).unlink()
            except OSError:
                pass
        return None, f"semantic model unavailable ({type(e).__name__}) — will re-download next run"

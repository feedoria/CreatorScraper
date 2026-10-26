"""Small helpers used across the UI: error log, dates, URLs, parsing."""

from datetime import datetime

from storage import data_dir


def log_error(where: str) -> str:
    """Append the current exception's full traceback to errors.log; returns its path."""
    import traceback
    path = data_dir() / "errors.log"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} · {where} =====\n")
            fh.write(traceback.format_exc())
    except OSError:
        pass
    return str(path)


def log_text(where: str, text: str) -> None:
    try:
        with open(data_dir() / "errors.log", "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} · {where} =====\n{text}\n")
    except OSError:
        pass


def short_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b")


def long_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b %Y")


def channel_url(r: dict) -> str:
    if r.get("url"):
        return r["url"]
    if r.get("handle"):
        return f"https://www.youtube.com/@{r['handle']}"
    return f"https://www.youtube.com/channel/{r['channel_id']}"


def split_lines(s: str) -> list[str]:
    return [x.strip() for x in s.splitlines() if x.strip()]


def to_int(s, default: int) -> int:
    try:
        return int(str(s).replace(".", "").replace(",", "").replace(" ", ""))
    except ValueError:
        return default

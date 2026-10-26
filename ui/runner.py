"""Running a search on a background thread and showing the results when it is done.

A mixin for the main App window (see app.py); `self` is the App."""

import queue
import threading
from datetime import datetime
from tkinter import messagebox

import enrich
import scout
from contacts import mark_shared_emails
from storage import save_settings
from youtube_api import ApiError, YouTubeClient

from ui.theme import APP, RED
from ui.util import log_error, log_text, split_lines


class Runner:
    def start(self):
        cfg = self._collect_cfg()
        save_settings(cfg)
        if not cfg["api_key"]:
            messagebox.showwarning(APP, "Add your YouTube API key in Settings first.")
            self.show("settings")
            return
        if not cfg["game_description"]:
            messagebox.showwarning(APP, "Write a game description — the match score is based on it.")
            return
        st = scout.Settings(
            game_name=cfg["game_name"],
            game_description=cfg["game_description"],
            comparable_games=split_lines(cfg["comparable_games"]),
            keywords=split_lines(cfg["keywords"]),
            tags=[t["name"] for t in cfg["tags"] if t["selected"]],
            min_subs=cfg["min_subs"], max_subs=cfg["max_subs"],
            max_inactive_days=cfg["max_inactive_days"],
            pages_per_query=cfg["pages_per_query"], max_deep=cfg["max_deep"],
            videos_per_channel=cfg["videos_per_channel"],
            language=cfg["language"],
            use_semantic=cfg["semantic"],
            exclude_ids=self.store.contacted_ids() if cfg["skip_contacted"] else set(),
        )
        client = YouTubeClient(cfg["api_key"], self.store)
        self.last_settings = st
        self.cancel.clear()
        self.b_run.config(state="disabled", text="●   SEARCHING…")
        self.b_stop.config(state="normal")
        self.pb["value"] = 0
        self._log(f"— Starting search ({datetime.now():%H:%M}) —")

        def work():
            try:
                prog = lambda m, fr=None: self.q.put(("progress", m, fr))
                prog("Looking up your comparable games on Steam (to spot store links in descriptions)…", 0.0)
                st.game_appids = self._steam_appids(st.comparable_games)
                ranked, stats = scout.run(st, client, prog, self.cancel, embedder_loader=self._load_embedder)
                stats["enrich"] = None
                if cfg["enrich"] and cfg["enrich_top"] > 0 and ranked and not self.cancel.is_set():
                    try:
                        stats["enrich"] = enrich.enrich(ranked, self.store, cfg["enrich_top"],
                                                        cfg["browser_fallback"], prog, self.cancel)
                    except Exception as e:  # optional step: keep the YouTube results
                        log_error("email search")
                        stats["enrich"] = {"failed": f"{type(e).__name__}"}
                self._apply_manual(ranked)
                stats["shared"] = mark_shared_emails(list(stats.get("all", {}).values()) or ranked)
                for c in ranked:
                    c.reasons = scout.reasons(c)
                self.q.put(("done", (ranked, stats), None))
            except scout.Cancelled:
                self.q.put(("error", "Search stopped.", None))
            except (ApiError, scout.InputError) as e:
                self.q.put(("error", str(e), None))
            except Exception as e:  # keep the GUI alive on anything unexpected
                path = log_error("search")
                self.q.put(("error", f"Unexpected error: {type(e).__name__}: {e}\n\n"
                                     f"Details were saved to:\n{path}", None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        self.cancel.set()
        self.l_status.config(text="Stopping…")

    def _reset_run_buttons(self):
        self.b_run.config(state="normal", text="▶   FIND & RANK")
        self.b_stop.config(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "progress":
                    self._log(a)
                    if b is not None:
                        self.pb["value"] = b
                    self.l_status.config(text=a[:90])
                elif kind == "done":
                    self._finish(*a)
                elif kind == "call":
                    a()
                elif kind == "error":
                    self._log("✖ " + a)
                    self.l_status.config(text=a[:90])
                    self._reset_run_buttons()
                    if a != "Search stopped.":
                        messagebox.showerror(APP, a)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _finish(self, ranked, stats):
        self.results = ranked
        self.last_stats = stats
        self._reset_run_buttons()
        self.l_status.config(text=f"Done · {len(ranked)} creators ranked.")
        with_email = sum(1 for c in ranked if c.emails)
        for key, val in (("found", stats["found"]), ("analysed", stats["analysed"]),
                         ("ranked", stats["ranked"]), ("emails", with_email), ("units", stats["units"])):
            self.kpi[key].config(text=f"{val:,}")
        msg = (f"{stats['in_size_range']} within the subscriber range · {stats['cache_hits']} requests served "
               f"free from cache · updated {datetime.now():%H:%M}")
        en = stats.get("enrich")
        if stats.get("shared"):
            msg = (f"{stats['shared']} email(s) used by several creators — kept and marked “Shared · agency?” "
                   "(probably a manager; check them)  ·  ") + msg
        if en and "failed" in en:
            msg = f"⚠ Email search on creators' pages failed ({en['failed']}) — YouTube results are complete  ·  " + msg
        elif en and en["checked"]:
            extra = f" (+{en['found_browser']} via browser)" if en["found_browser"] else ""
            note = " · browser fallback unavailable (details in errors.log)" if en["browser"] == "unavailable" else ""
            if en.get("browser_error"):
                log_text("browser fallback", en["browser_error"])
            msg = (f"Emails found on creators' pages: {en['found_http'] + en['found_browser']} of "
                   f"{en['checked']} checked{extra}{note}  ·  ") + msg
        mdl = stats.get("model")
        if mdl is not None:
            if mdl.learned:
                tops = ", ".join(f"{name.lower()} ({'+' if w > 0 else '−'})" for name, w in mdl.summary(3))
                mtxt = (f"Model learned from {mdl.rows:,} examples across {len(mdl.games)} similar games — "
                        f"strongest signals: {tops}")
            viral = [f"{g} (×{w:.1f})" for g, w in sorted(mdl.weights.items(), key=lambda t: t[1]) if w < 0.8]
            if viral:
                mtxt += " · counted less because almost everyone played it: " + ", ".join(viral[:2])
            else:
                mtxt = "Model: default weights (not enough data to learn — add more comparable games)"
            msg = f"{mtxt}  ·  {stats.get('semantic', 'off')}\n" + msg
        if stats["quota_hit"]:
            msg = "⚠ Daily quota used up — partial results; run again tomorrow to continue from cache.  ·  " + msg
        self.res_sub.config(text=msg)
        self.l_units.config(text=f"Last run: {stats['units']} API units")
        self.nav["results"][4].config(text=f" {len(ranked)} ", bg=RED)
        self._log(msg)
        self._render_results()
        self.show("results")

"""The Email template and Settings pages, and saving the configuration.

A mixin for the main App window (see app.py); `self` is the App."""

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

import embed
import outreach
import scout
from storage import data_dir, save_settings

from ui.theme import GREEN, KEY_HELP, MONO, MUTED, RED, SURFACE
from ui.util import to_int


class SettingsPage:
    _embedder = None  # the MiniLM model, loaded once and shared by every search

    # ---------------------------------------------------------- template page
    def _build_template_page(self):
        f = self._page("template", "Email template",
                       "A draft is generated for every creator. Send them by hand and personalise them — never in bulk.")
        p = self.px
        o, c = self._card(f, "Available fields")
        o.pack(fill="x")
        ttk.Label(c, style="CardMuted.TLabel", font=(MONO, 9), justify="left", text=(
            "{creator}  channel name             {game}  game name            {hook}  short genre\n"
            "{matched}  first similar game played  {matched_games}  all of them  {pitch}  game description"
        )).pack(anchor="w")
        o, c = self._card(f)
        o.pack(fill="both", expand=True, pady=(p(14), 0))
        self.t_tpl = self._text(c, font=(MONO, 10), bg=SURFACE, highlightthickness=0)
        self.t_tpl.pack(fill="both", expand=True)
        self.t_tpl.insert("1.0", self.cfg.get("template", scout.DEFAULT_TEMPLATE))
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(p(14), 0))
        ttk.Button(bar, text="Save template", style="Accent.TButton",
                   command=self._save_cfg).pack(side="left")
        ttk.Button(bar, text="Reset to default", style="Ghost.TButton", command=lambda: (
            self.t_tpl.delete("1.0", "end"), self.t_tpl.insert("1.0", scout.DEFAULT_TEMPLATE))
        ).pack(side="left", padx=p(10))

    # ---------------------------------------------------------- settings page
    def _build_settings_page(self):
        page = self._page("settings", "Settings",
                          "A YouTube Data API v3 key is free and takes about 3 minutes to set up.")
        f = self._scrollable(page, "settings")
        p = self.px
        o, c = self._card(f, "YouTube Data API v3 key")
        o.pack(fill="x")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x")
        self.v_key = tk.StringVar(value=self.cfg.get("api_key", ""))
        self.e_key = ttk.Entry(row, textvariable=self.v_key, show="•", font=(MONO, 11))
        self.e_key.pack(side="left", fill="x", expand=True)
        self.v_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="show", variable=self.v_show, style="Card.TCheckbutton",
                        command=lambda: self.e_key.config(show="" if self.v_show.get() else "•")
                        ).pack(side="left", padx=p(12))
        ttk.Button(row, text="Save", style="Accent.TButton", command=self._save_key).pack(side="left")
        self.v_key.trace_add("write", lambda *a: self._refresh_key_status())

        o, c = self._card(f, "How to get a key")
        o.pack(fill="x", pady=(p(14), 0))
        steps = [
            "Go to console.cloud.google.com and create (or pick) a project.",
            "APIs & Services → Library → search “YouTube Data API v3” → Enable.",
            "Credentials → Create credentials → API key  (if asked: Public data).",
            "Edit API key → API restrictions → Restrict key → tick only YouTube Data API v3 → Save.",
            "Paste the key above and click Save.",
        ]
        for i, s in enumerate(steps, 1):
            r = ttk.Frame(c, style="Card.TFrame")
            r.pack(fill="x", pady=p(3))
            tk.Label(r, text=f"{i:02d}", bg=SURFACE, fg=RED, font=(MONO, 10, "bold")).pack(side="left")
            ttk.Label(r, text=s, style="Card.TLabel").pack(side="left", padx=p(12))
        ttk.Label(c, style="CardMuted.TLabel", justify="left", text=(
            "Free quota: 10,000 units/day · one search = 100 · analysing a channel ≈ 2 · "
            "results are cached for a few days.")).pack(anchor="w", pady=(p(10), 0))
        ttk.Button(c, text="Open the API page in your browser ↗", style="Ghost.TButton",
                   command=lambda: webbrowser.open(KEY_HELP)).pack(anchor="w", pady=(p(12), 0))

        o, c = self._card(f, "Prediction", "how creators are ranked")
        o.pack(fill="x", pady=(p(14), 0))
        ttk.Label(c, style="Card.TLabel", justify="left", wraplength=p(900), text=(
            "Every search trains a small model on the public YouTube coverage of your comparable games: "
            "it hides one game at a time and learns which signals predict who covered it. "
            "That gives each creator a coverage chance, predicted views and expected views per pitch.\n\n"
            "Coverage chance = how likely a channel like this is to cover a game like yours, judged by who "
            "covered the comparable games. It cannot know whether they will read your email: that part "
            "is covered by 'Accepted dev keys before' and by sorting smaller channels higher in expected views.")
        ).pack(anchor="w")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(p(12), 0))
        self.v_sem = tk.BooleanVar(value=self.cfg.get("semantic", True))
        ttk.Checkbutton(row, text="Use the pretrained semantic model (MiniLM, ~23 MB, downloaded once)",
                        variable=self.v_sem, style="Card.TCheckbutton").pack(side="left")
        self.l_sem = ttk.Label(row, style="CardMuted.TLabel")
        self.l_sem.pack(side="left", padx=p(14))
        self.b_sem = ttk.Button(row, text="Download now", style="Ghost.TButton", command=self._download_model)
        self.b_sem.pack(side="right")
        self._refresh_sem_status()

        o, c = self._card(f, "Outreach", "follow-ups, email drafts, published videos")
        o.pack(fill="x", pady=(p(14), 0))
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Remind me to follow up after", style="Card.TLabel").pack(side="left")
        self.v_fu_days = tk.StringVar(value=str(self.cfg.get("followup_days", 7)))
        fu = ttk.Spinbox(row, from_=0, to=60, width=4, textvariable=self.v_fu_days,
                         command=lambda: self._render_contacts())
        fu.pack(side="left", padx=p(8))
        fu.bind("<FocusOut>", lambda e: self._render_contacts())
        ttk.Label(row, text="days without an answer  (0 = never)", style="CardMuted.TLabel").pack(side="left")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(p(10), 0))
        ttk.Label(row, text="“Write email” opens", style="Card.TLabel").pack(side="left")
        self.v_mail = tk.StringVar(value=self.cfg.get("mail_app", outreach.MAIL_APPS[0]))
        mb = ttk.Combobox(row, textvariable=self.v_mail, values=outreach.MAIL_APPS, state="readonly", width=20)
        mb.pack(side="left", padx=p(8))
        mb.bind("<<ComboboxSelected>>", lambda e: mb.selection_clear())
        ttk.Label(row, text="a draft you review and send yourself — nothing is ever sent automatically",
                  style="CardMuted.TLabel").pack(side="left")
        self.v_autovid = tk.BooleanVar(value=self.cfg.get("auto_video_check", True))
        ttk.Checkbutton(c, text="Once a day, look for videos about my game from creators I contacted (≈2 units each)",
                        variable=self.v_autovid, style="Card.TCheckbutton").pack(anchor="w", pady=(p(10), 0))

        o, c = self._card(f, "Local data", "cache, contacted history, settings")
        o.pack(fill="x", pady=(p(14), 0))
        ttk.Label(c, text=str(data_dir()), style="CardMuted.TLabel", font=(MONO, 9)).pack(anchor="w")
        ttk.Button(c, text="Clear API cache", style="Ghost.TButton",
                   command=self._clear_cache).pack(anchor="w", pady=(p(10), 0))

    # ------------------------------------------------------------- actions
    def _collect_cfg(self) -> dict:
        self.cfg.update(
            api_key=self.v_key.get().strip(),
            game_name=self.e_name.get().strip(),
            hook=self.e_hook.get().strip(),
            game_description=self.t_desc.get("1.0", "end").strip(),
            steam_ref=self.v_steam.get().strip(),
            comparable_games=self.t_games.get("1.0", "end").strip(),
            keywords="\n".join(self._search_keywords()),
            tags=[dict(t) for t in self.tags],
            search_tags=self.v_search_tags.get(),
            min_subs=to_int(self.v_min.get(), 500),
            max_subs=to_int(self.v_max.get(), 2_000_000),
            max_inactive_days=to_int(self.v_inactive.get(), 90),
            pages_per_query=max(1, min(5, to_int(self.v_pages.get(), 1))),
            max_deep=max(10, min(1000, to_int(self.v_deep.get(), 150))),
            videos_per_channel=max(10, min(500, to_int(self.v_videos.get(), 100))),
            language=self.v_lang.get().strip().lower(),
            skip_contacted=self.v_skip.get(),
            enrich=self.v_enrich.get(),
            enrich_top=max(0, min(500, to_int(self.v_enrich_n.get(), 50))),
            browser_fallback=self.v_browser.get(),
            semantic=self.v_sem.get(),
            sort_by=self.v_sort.get(),
            top_n=to_int(self.v_top.get(), 10),
            template=self.t_tpl.get("1.0", "end").rstrip() + "\n",
            followup_days=max(0, min(60, to_int(self.v_fu_days.get(), 7))),
            mail_app=self.v_mail.get(),
            auto_video_check=self.v_autovid.get(),
        )
        return self.cfg

    def _save_cfg(self):
        save_settings(self._collect_cfg())
        self._log("Settings saved.")
        self._toast("Saved ✓")

    def _save_key(self):
        self._save_cfg()
        self._refresh_key_status()
        if self.v_key.get().strip():
            self.show("search")

    def _clear_cache(self):
        self.store.cache_clear()
        self._toast("Cache cleared")

    def _load_embedder(self, progress):
        if SettingsPage._embedder is None:
            emb, status = embed.load_embedder(progress)
            if emb is None:
                return None, status
            SettingsPage._embedder = emb
            self.q.put(("call", self._refresh_sem_status, None))
        return SettingsPage._embedder, "semantic model on (MiniLM)"

    def _refresh_sem_status(self):
        if embed.is_downloaded():
            self.l_sem.config(text="● downloaded", foreground=GREEN)
            self.b_sem.pack_forget()
        else:
            self.l_sem.config(text="not downloaded yet — happens automatically on the first search",
                              foreground=MUTED)

    def _download_model(self):
        self.b_sem.config(state="disabled", text="Downloading…")

        def work():
            emb, status = embed.load_embedder()
            if emb is not None:
                SettingsPage._embedder = emb
            self.q.put(("call", lambda: (self.b_sem.config(state="normal", text="Download now"),
                                   self._refresh_sem_status(),
                                   self._toast("Semantic model ready" if emb else status[:60])), None))
        threading.Thread(target=work, daemon=True).start()

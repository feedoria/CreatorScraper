"""The “Your game” page: game details, comparable games, Steam tags, filters.

A mixin for the main App window (see app.py); `self` is the App."""

import threading
import tkinter as tk
from tkinter import ttk

import steam

from ui.theme import BG, BORDER, MUTED, RED, SURFACE2, TEXT, UI, UI_BLACK, UI_SEMI
from ui.util import to_int, split_lines, log_error


class SearchPage:
    # ------------------------------------------------------------ search page
    def _build_search_page(self):
        f = self._page("search", "Your game",
                       "Describe your game and similar games — we use them to find and rank creators.")
        p = self.px

        top = ttk.Frame(f)
        top.pack(fill="x")
        top.columnconfigure(0, weight=3)
        top.columnconfigure(1, weight=2)
        o, c = self._card(top)
        o.grid(row=0, column=0, sticky="nsew", padx=(0, p(8)))
        ttk.Label(c, text="GAME NAME", style="Field.TLabel").pack(anchor="w")
        self.e_name = ttk.Entry(c, font=(UI_SEMI, 12))
        self.e_name.pack(fill="x", pady=(p(4), 0))
        self.e_name.insert(0, self.cfg.get("game_name", ""))
        o, c = self._card(top)
        o.grid(row=0, column=1, sticky="nsew", padx=(p(8), 0))
        ttk.Label(c, text="SHORT GENRE  ·  used in the email", style="Field.TLabel").pack(anchor="w")
        self.e_hook = ttk.Entry(c, font=(UI_SEMI, 12))
        self.e_hook.pack(fill="x", pady=(p(4), 0))
        self.e_hook.insert(0, self.cfg.get("hook", ""))

        o, c = self._card(f, "Game description", "like your Steam page — the match score is based on it")
        o.pack(fill="x", pady=(p(14), 0))
        self.t_desc = self._text(c, height=4)
        self.t_desc.pack(fill="x")
        self.t_desc.insert("1.0", self.cfg.get("game_description", ""))

        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, pady=(p(14), 0))
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)
        o, c = self._card(mid, "Comparable games", "one per line · strongest signal")
        o.grid(row=0, column=0, sticky="nsew", padx=(0, p(8)))
        self.t_games = self._text(c, height=5)
        self.t_games.pack(fill="both", expand=True)
        self.t_games.insert("1.0", self.cfg.get("comparable_games", ""))
        o, c = self._card(mid, "Keywords & Steam tags", "click a tag to select it · right-click removes")
        o.grid(row=0, column=1, sticky="nsew", padx=(p(8), 0))
        self._build_tags(c)

        o, c = self._card(f, "Filters")
        self._filters_card = o
        self.v_min = tk.StringVar(value=str(self.cfg.get("min_subs", 500)))
        self.v_max = tk.StringVar(value=str(self.cfg.get("max_subs", 2_000_000)))
        self.v_inactive = tk.StringVar(value=str(self.cfg.get("max_inactive_days", 90)))
        self.v_pages = tk.StringVar(value=str(self.cfg.get("pages_per_query", 1)))
        self.v_deep = tk.StringVar(value=str(self.cfg.get("max_deep", 150)))
        self.v_videos = tk.StringVar(value=str(self.cfg.get("videos_per_channel", 100)))
        self.v_lang = tk.StringVar(value=self.cfg.get("language", "en"))
        self.v_skip = tk.BooleanVar(value=self.cfg.get("skip_contacted", True))
        grid = ttk.Frame(c, style="Card.TFrame")
        grid.pack(fill="x")
        fields = [
            ("MIN SUBSCRIBERS", self.v_min), ("MAX SUBSCRIBERS", self.v_max),
            ("MAX INACTIVE (DAYS)", self.v_inactive), ("PAGES / SEARCH", self.v_pages),
            ("CHANNELS ANALYSED", self.v_deep), ("VIDEOS PER CHANNEL", self.v_videos),
            ("LANGUAGE (en / ro / empty)", self.v_lang),
        ]
        for i, (lbl, var) in enumerate(fields):
            grid.columnconfigure(i, weight=1)
            cell = ttk.Frame(grid, style="Card.TFrame")
            cell.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else p(6), 0))
            ttk.Label(cell, text=lbl, style="Field.TLabel").pack(anchor="w")
            ttk.Entry(cell, textvariable=var, width=8).pack(fill="x", pady=(p(4), 0))
        bottom = ttk.Frame(c, style="Card.TFrame")
        bottom.pack(fill="x", pady=(p(12), 0))
        ttk.Checkbutton(bottom, text="Skip creators already contacted", variable=self.v_skip,
                        style="Card.TCheckbutton").pack(side="left")
        self.l_cost = ttk.Label(bottom, style="CardMuted.TLabel")
        self.l_cost.pack(side="right")
        self.v_enrich = tk.BooleanVar(value=self.cfg.get("enrich", True))
        self.v_enrich_n = tk.StringVar(value=str(self.cfg.get("enrich_top", 50)))
        self.v_browser = tk.BooleanVar(value=self.cfg.get("browser_fallback", True))
        row2 = ttk.Frame(c, style="Card.TFrame")
        row2.pack(fill="x", pady=(p(8), 0))
        ttk.Checkbutton(row2, text="Find emails on creators' Linktree / website for the top",
                        variable=self.v_enrich, style="Card.TCheckbutton").pack(side="left")
        ttk.Entry(row2, textvariable=self.v_enrich_n, width=5).pack(side="left", padx=p(8))
        ttk.Checkbutton(row2, text="Browser fallback for JavaScript pages (Selenium)",
                        variable=self.v_browser, style="Card.TCheckbutton").pack(side="left", padx=(p(16), 0))
        self.t_games.bind("<KeyRelease>", lambda e: self._update_cost())
        self.v_pages.trace_add("write", lambda *a: self._update_cost())
        self.v_deep.trace_add("write", lambda *a: self._update_cost())
        self.v_videos.trace_add("write", lambda *a: self._update_cost())

        bar = ttk.Frame(f)
        bar.columnconfigure(2, weight=1)
        self.b_run = ttk.Button(bar, text="▶   FIND & RANK", style="Accent.TButton", command=self.start)
        self.b_run.grid(row=0, column=0)
        self.b_stop = ttk.Button(bar, text="Stop", style="Ghost.TButton", command=self.stop,
                                 state="disabled")
        self.b_stop.grid(row=0, column=1, padx=p(10))
        prog = ttk.Frame(bar)
        prog.grid(row=0, column=2, sticky="ew", padx=(p(10), 0))
        self.l_status = ttk.Label(prog, text="Ready to search.", style="Muted.TLabel", font=(UI, 9))
        self.l_status.pack(anchor="w")
        self.pb = ttk.Progressbar(prog, mode="determinate", maximum=1.0,
                                  style="Red.Horizontal.TProgressbar")
        self.pb.pack(fill="x", pady=(p(4), 0))

        # pack bottom-up so the run button never falls off a small/scaled screen
        mid.pack_forget()
        bar.pack(side="bottom", fill="x", pady=(p(16), 0))
        self._filters_card.pack(side="bottom", fill="x", pady=(p(14), 0))
        mid.pack(fill="both", expand=True, pady=(p(14), 0))
        self.log_lines: list[str] = []
        self._update_cost()

    # ------------------------------------------------------------ tags
    def _build_tags(self, c):
        p = self.px
        if "tags" not in self.cfg:   # older settings: the keyword lines become selected manual tags
            self.cfg["tags"] = [{"name": k, "source": "manual", "count": 0, "selected": True}
                                for k in split_lines(self.cfg.get("keywords", ""))]
            self.cfg.setdefault("search_tags", True)
        self.tags = [dict(t) for t in self.cfg.get("tags", [])]

        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x")
        self.v_steam = tk.StringVar(value=self.cfg.get("steam_ref", ""))
        e = ttk.Entry(row, textvariable=self.v_steam)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self._fetch_tags(False))
        self.b_tags = ttk.Button(row, text="Get tags", style="Ghost.TButton", command=lambda: self._fetch_tags(False))
        self.b_tags.pack(side="left", padx=(p(6), 0))
        self.b_tags2 = ttk.Button(row, text="From comparable games", style="Ghost.TButton",
                                  command=lambda: self._fetch_tags(True))
        self.b_tags2.pack(side="left", padx=(p(6), 0))
        ttk.Label(c, text="Steam store link or game name (your game, if it's on Steam)",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(p(2), p(6)))

        self.t_chips = tk.Text(c, height=4, bg=SURFACE2, relief="flat", borderwidth=0, highlightthickness=1,
                               highlightbackground=BORDER, padx=p(6), pady=p(6), wrap="word", cursor="arrow",
                               font=(UI, 10))
        self.t_chips.pack(fill="both", expand=True)

        add = ttk.Frame(c, style="Card.TFrame")
        add.pack(fill="x", pady=(p(8), 0))
        self.v_newtag = tk.StringVar()
        ne = ttk.Entry(add, textvariable=self.v_newtag)
        ne.pack(side="left", fill="x", expand=True)
        ne.bind("<Return>", lambda ev: self._add_tag())
        ttk.Button(add, text="+ Add keyword", style="Ghost.TButton", command=self._add_tag).pack(side="left", padx=(p(6), 0))
        ttk.Button(add, text="Clear unselected", style="Ghost.TButton",
                   command=self._clear_unselected).pack(side="left", padx=(p(6), 0))
        self.b_browse = ttk.Button(c, text="☰  Browse all Steam tags", style="Ghost.TButton",
                                   command=self._browse_tags)
        self.b_browse.pack(anchor="w", pady=(p(8), 0))
        self.v_search_tags = tk.BooleanVar(value=self.cfg.get("search_tags", False))
        ttk.Checkbutton(c, text="Also search YouTube for the selected keywords (100 units each)",
                        variable=self.v_search_tags, style="Card.TCheckbutton",
                        command=self._update_cost).pack(anchor="w", pady=(p(8), 0))
        self.l_tags = ttk.Label(c, text="", style="CardMuted.TLabel")
        self.l_tags.pack(anchor="w")
        self._render_tags()

    def _render_tags(self):
        t = self.t_chips
        t.config(state="normal")
        for w in t.winfo_children():
            w.destroy()
        t.delete("1.0", "end")
        if not self.tags:
            t.insert("end", "No keywords yet — get them from Steam or add your own below.")
            t.config(fg=MUTED)
        for i, tag in enumerate(self.tags):
            label = tag["name"] + (f"  {tag['count']}" if tag.get("count") else "")
            sel = tag["selected"]
            chip = tk.Label(t, text=label, bg=RED if sel else BORDER, fg="white" if sel else MUTED,
                            font=(UI_SEMI if sel else UI, 9), padx=self.px(10), pady=self.px(3), cursor="hand2")
            chip.bind("<Button-1>", lambda e, k=i: self._toggle_tag(k))
            chip.bind("<Button-3>", lambda e, k=i: self._remove_tag(k))
            chip.bind("<Button-2>", lambda e, k=i: self._remove_tag(k))
            t.window_create("end", window=chip, padx=self.px(3), pady=self.px(3))
        t.config(state="disabled")
        n = sum(t_["selected"] for t_ in self.tags)
        self.l_tags.config(text=f"{n} selected — used to match creators' content (free)"
                           + (" and as YouTube searches" if self.v_search_tags.get() and n else ""))
        self._update_cost() if hasattr(self, "l_cost") else None

    def _toggle_tag(self, i):
        self.tags[i]["selected"] = not self.tags[i]["selected"]
        self._render_tags()

    def _remove_tag(self, i):
        del self.tags[i]
        self._render_tags()

    def _clear_unselected(self):
        self.tags = [t for t in self.tags if t["selected"]]
        self._render_tags()

    def _add_tag(self):
        names = [x.strip() for x in self.v_newtag.get().replace(";", ",").split(",") if x.strip()]
        for n in names:
            existing = next((t for t in self.tags if t["name"].lower() == n.lower()), None)
            if existing:
                existing["selected"] = True
            else:
                self.tags.append({"name": n, "source": "manual", "count": 0, "selected": True})
        self.v_newtag.set("")
        self._render_tags()

    def _merge_tags(self, names_counts, source):
        known = {t["name"].lower(): t for t in self.tags}
        for name, count in names_counts:
            if name.lower() in known:
                known[name.lower()]["count"] = count or known[name.lower()].get("count", 0)
            else:
                t = {"name": name, "source": source, "count": count, "selected": False}
                self.tags.append(t)
                known[name.lower()] = t
        self._render_tags()

    def _browse_tags(self):
        self.b_browse.config(state="disabled", text="Loading Steam tags…")
        st = steam.Steam(self.store)

        def work():
            try:
                names, live = st.all_tags()
            except Exception:
                log_error("steam all tags")
                names, live = [], False
            self.q.put(("call", lambda: self._tags_dialog(names, live), None))
        threading.Thread(target=work, daemon=True).start()

    def _tags_dialog(self, names: list[str], live: bool):
        self.b_browse.config(state="normal", text="☰  Browse all Steam tags")
        p = self.px
        d = tk.Toplevel(self, bg=BG)
        d.title("All Steam tags")
        d.transient(self)
        d.geometry(f"{p(860)}x{p(620)}+{self.winfo_rootx() + p(120)}+{self.winfo_rooty() + p(60)}")
        d.configure(bg=BG)
        box = tk.Frame(d, bg=BG, padx=p(22), pady=p(18))
        box.pack(fill="both", expand=True)
        tk.Label(box, text="All Steam tags", bg=BG, fg=TEXT, font=(UI_BLACK, 16)).pack(anchor="w")
        tk.Label(box, bg=BG, fg=MUTED, font=(UI, 9), text=(
            f"{len(names)} tags " + ("from the Steam store" if live else "(offline list — Steam couldn't be reached)")
            + " · click to select · selected tags are added to your keywords")).pack(anchor="w", pady=(p(2), p(10)))
        top = tk.Frame(box, bg=BG)
        top.pack(fill="x")
        q = tk.StringVar()
        ent = ttk.Entry(top, textvariable=q, font=(UI, 11))
        ent.pack(side="left", fill="x", expand=True)
        ent.focus_set()
        only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Selected only", variable=only, command=lambda: draw()).pack(side="left", padx=p(12))
        count = tk.Label(box, text="", bg=BG, fg=MUTED, font=(UI, 9))
        count.pack(anchor="w", pady=(p(6), p(4)))
        txt = tk.Text(box, bg=SURFACE2, relief="flat", borderwidth=0, highlightthickness=1,
                      highlightbackground=BORDER, padx=p(8), pady=p(8), wrap="word", cursor="arrow")
        ys = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=ys.set)
        btns = tk.Frame(box, bg=BG)
        btns.pack(side="bottom", fill="x", pady=(p(12), 0))
        ys.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)

        def is_sel(name):
            return any(t["name"].lower() == name.lower() and t["selected"] for t in self.tags)

        def toggle(name, chip):
            t = next((t for t in self.tags if t["name"].lower() == name.lower()), None)
            if t is None:
                self.tags.append({"name": name, "source": "steam", "count": 0, "selected": True})
            else:
                t["selected"] = not t["selected"]
            sel = is_sel(name)
            chip.config(bg=RED if sel else BORDER, fg="white" if sel else MUTED, font=(UI_SEMI if sel else UI, 9))
            update_count()

        def update_count():
            n = sum(1 for nm in names if is_sel(nm))
            count.config(text=f"{n} selected")

        def draw():
            txt.config(state="normal")
            for w in txt.winfo_children():
                w.destroy()
            txt.delete("1.0", "end")
            needle = q.get().strip().lower()
            shown = [nm for nm in names if needle in nm.lower() and (not only.get() or is_sel(nm))]
            for nm in shown:
                sel = is_sel(nm)
                chip = tk.Label(txt, text=nm, bg=RED if sel else BORDER, fg="white" if sel else MUTED,
                                font=(UI_SEMI if sel else UI, 9), padx=p(10), pady=p(3), cursor="hand2")
                chip.bind("<Button-1>", lambda e, n_=nm, c_=chip: toggle(n_, c_))
                txt.window_create("end", window=chip, padx=p(3), pady=p(3))
            if not shown:
                txt.insert("end", "No tag matches." if names else "Couldn't load the tag list.")
            txt.config(state="disabled")
            update_count()

        pending = {"id": None}

        def schedule(*_):
            if pending["id"]:
                d.after_cancel(pending["id"])
            pending["id"] = d.after(150, draw)
        q.trace_add("write", schedule)

        def close():
            self._render_tags()
            d.destroy()
        ttk.Button(btns, text="Done", style="Accent.TButton", command=close).pack(side="right")
        d.protocol("WM_DELETE_WINDOW", close)
        d.bind("<Escape>", lambda e: close())
        self._tags_dlg = d
        draw()

    def _steam_appids(self, games) -> dict:
        """Comparable game -> Steam appid; best effort (cached a week, never blocks a search)."""
        out, sm = {}, steam.Steam(self.store)
        for g in [x.strip() for x in games if x.strip()]:
            try:
                found = sm.find_app(g)
            except Exception:
                break          # Steam unreachable: skip the rest
            norm = lambda t: "".join(ch for ch in t.lower() if ch.isalnum())
            if found and norm(found[1]) == norm(g):   # exact game only, never a soundtrack/DLC
                out[g] = found[0]
        return out

    def _search_keywords(self) -> list[str]:
        if not hasattr(self, "v_search_tags") or not self.v_search_tags.get():
            return []
        return [t["name"] for t in self.tags if t["selected"]]

    def _fetch_tags(self, from_games: bool):
        st = steam.Steam(self.store)
        ref = self.v_steam.get().strip()
        games = split_lines(self.t_games.get("1.0", "end"))
        if not from_games and not ref:
            self._toast("Paste a Steam link or type a game name first")
            return
        if from_games and not games:
            self._toast("Add some comparable games first")
            return
        for b in (self.b_tags, self.b_tags2):
            b.config(state="disabled")
        self.l_tags.config(text="Asking Steam…")

        def work():
            try:
                if from_games:
                    counts, missing = st.tags_for_games(games)
                    res = ([(t, n) for t, n in counts.most_common(30)], None,
                           f"Tags shared by your {len(games) - len(missing)} comparable games (number = how many have it)"
                           + (f" · not found on Steam: {', '.join(missing)}" if missing else ""))
                else:
                    name, tags = st.tags_for(ref)
                    res = ([(t, 0) for t in tags], None, f"Steam tags of {name}" if tags else
                           f"{name} has no tags on Steam yet")
            except steam.SteamError as e:
                res = ([], str(e), "")
            except Exception as e:
                log_error("steam tags")
                res = ([], f"Steam lookup failed ({type(e).__name__})", "")
            self.q.put(("call", lambda: self._tags_done(*res), None))
        threading.Thread(target=work, daemon=True).start()

    def _tags_done(self, pairs, err, note):
        for b in (self.b_tags, self.b_tags2):
            b.config(state="normal")
        if err:
            self._render_tags()
            self.l_tags.config(text="⚠ " + err)
            return
        self._merge_tags(pairs, "steam")
        if note:
            self.l_tags.config(text=note + " — click the ones that fit")

    def _update_cost(self):
        n = len(split_lines(self.t_games.get("1.0", "end"))) + len(self._search_keywords())
        pages = to_int(self.v_pages.get(), 1)
        deep = to_int(self.v_deep.get(), 150)
        vids = max(10, min(500, to_int(self.v_videos.get(), 100)))
        units = n * pages * 100 + deep * 2 * -(-vids // 50) + 10
        warn = "  ⚠ over the daily quota" if units > 10_000 else ""
        self.l_cost.config(text=f"Estimated cost ≈ {units:,} / 10,000 units today{warn}  ·  repeat runs are free from cache")

"""The Results page: ranked table, search/sort/filter, creator details, email review, export.

A mixin for the main App window (see app.py); `self` is the App."""

import csv
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import lookup
import model as cov_model
import outreach
import scout
from contacts import CONF_ORDER, EMAIL_RE
from youtube_api import YouTubeClient

from ui.theme import (
    APP,
    CONF_LABEL,
    EMAIL_FILTERS,
    FAINT,
    GREEN,
    HEADS,
    MONO,
    MUTED,
    RED,
    RED_TEXT,
    RED_TINT,
    SORTS,
    SURFACE,
    TEXT,
    TEXT_COLS,
    UI,
    UI_BLACK,
    UI_SEMI,
)
from ui.util import to_int, long_date, short_date


class ResultsPage:
    # ----------------------------------------------------------- results page
    def _build_results_page(self):
        f = self._page("results", "Results", "You haven't run a search yet.")
        p = self.px
        self.res_sub = f.subtitle
        self.res_sub.configure(wraplength=p(1100), justify="left")

        kpis = ttk.Frame(f)
        kpis.pack(fill="x")
        self.kpi = {}
        for i, (key, label) in enumerate((("found", "CHANNELS FOUND"), ("analysed", "ANALYSED"),
                                          ("ranked", "RANKED"), ("emails", "WITH PUBLIC EMAIL"),
                                          ("units", "API UNITS USED"))):
            kpis.columnconfigure(i, weight=1, uniform="kpi")
            o, c = self._card(kpis, pad=14)
            o.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else p(6), 0))
            num = ttk.Label(c, text="—", style="KpiRed.TLabel" if key == "ranked" else "KpiNum.TLabel")
            num.pack(anchor="w")
            ttk.Label(c, text=label, style="KpiLbl.TLabel").pack(anchor="w")
            self.kpi[key] = num

        srow = ttk.Frame(f)
        srow.pack(fill="x", pady=(p(16), 0))
        ttk.Label(srow, text="⌕  Search", style="Muted.TLabel").pack(side="left")
        self.v_q = tk.StringVar()
        qe = ttk.Entry(srow, textvariable=self.v_q, width=40)
        qe.pack(side="left", padx=p(8))
        ttk.Button(srow, text="✕", style="Ghost.TButton", width=3,
                   command=lambda: self.v_q.set("")).pack(side="left")
        ttk.Label(srow, text="name, @handle, email or a pasted channel link", style="Muted.TLabel",
                  font=(UI, 9)).pack(side="left", padx=p(12))
        self.l_count = ttk.Label(srow, text="", style="Muted.TLabel")
        self.l_count.pack(side="right")
        self.v_q.trace_add("write", lambda *a: self._render_results())
        self.why_bar = ttk.Frame(f, style="Card.TFrame", padding=(p(14), p(10)))
        self.b_add = ttk.Button(self.why_bar, text="+ Add to results (≈3 units)", style="Accent.TButton",
                                command=self.add_to_results)
        self.b_add.pack(side="right")
        self.b_why = ttk.Button(self.why_bar, text="Why isn't it here?", style="Ghost.TButton",
                                command=self.why_missing)
        self.b_why.pack(side="right", padx=p(8))
        self.why_text = tk.Label(self.why_bar, text="", bg=SURFACE, fg=TEXT, font=(UI, 10), justify="left",
                                 anchor="w", wraplength=p(640))
        self.why_text.pack(side="left", fill="x", expand=True)
        self._srow = srow

        tools = ttk.Frame(f)
        tools.pack(fill="x", pady=(p(10), p(10)))
        ttk.Label(tools, text="Highlight top", style="Muted.TLabel").pack(side="left")
        self.v_top = tk.StringVar(value=str(self.cfg.get("top_n", 10)))
        sp = ttk.Spinbox(tools, from_=1, to=500, width=4, textvariable=self.v_top,
                         command=self._render_results)
        sp.pack(side="left", padx=p(8))
        sp.bind("<Return>", lambda e: self._render_results())
        sp.bind("<FocusOut>", lambda e: self._render_results())
        ttk.Label(tools, text="Show", style="Muted.TLabel").pack(side="left", padx=(p(14), p(8)))
        self.v_efilter = tk.StringVar(value=EMAIL_FILTERS[0])
        fb = ttk.Combobox(tools, textvariable=self.v_efilter, values=EMAIL_FILTERS, state="readonly", width=16)
        fb.pack(side="left", padx=(0, p(14)))
        fb.bind("<<ComboboxSelected>>", lambda e: (fb.selection_clear(), self._render_results()))
        ttk.Label(tools, text="Sort by", style="Muted.TLabel").pack(side="left", padx=(p(6), p(8)))
        self.v_sort = tk.StringVar(value=self.cfg.get("sort_by", SORTS[0]))
        cb = ttk.Combobox(tools, textvariable=self.v_sort, values=SORTS, state="readonly", width=24)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: (cb.selection_clear(), self._set_sort_col(None)))
        ttk.Button(tools, text="Export CSV · all", style="Ghost.TButton",
                   command=lambda: self.export(False)).pack(side="right")
        ttk.Button(tools, text="↓  Export CSV · top", style="Accent.TButton",
                   command=lambda: self.export(True)).pack(side="right", padx=p(8))

        pw = ttk.PanedWindow(f, orient="vertical")
        pw.pack(fill="both", expand=True)

        o, tf = self._card(pw, pad=0)
        cols = ("rank", "channel", "chance", "exp", "pred", "subs", "keys", "games", "last", "email",
                "source", "check", "done", "match")
        heads = tuple(HEADS[c] for c in cols)
        widths = (44, 190, 76, 92, 96, 70, 56, 200, 96, 220, 84, 104, 90, 66)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for col, h, w in zip(cols, heads, widths):
            self.tree.heading(col, text=h, anchor="w" if col in ("channel", "games", "email") else "center",
                              command=lambda c=col: self._click_heading(c))
            self.tree.column(col, width=p(w), minwidth=p(40),
                             anchor="w" if col in ("channel", "games", "email") else "center")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        xs.pack(side="bottom", fill="x")
        ys.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("top", background=RED_TINT)
        self.tree.tag_configure("odd", background=SURFACE)
        self.tree.tag_configure("even", background="#17171c")
        self.tree.tag_configure("done", foreground=FAINT)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())
        self.tree.bind("<Double-1>", lambda e: self.open_channel())
        for k in ("v", "V"):
            self.tree.bind(f"<Key-{k}>", lambda e: self.email_ok())
        for k in ("x", "X"):
            self.tree.bind(f"<Key-{k}>", lambda e: self.email_wrong())
        for k in ("o", "O"):
            self.tree.bind(f"<Key-{k}>", lambda e: self.open_email_source())
        pw.add(o, weight=3)

        o, df = self._card(pw, pad=14)
        btns = ttk.Frame(df, style="Card.TFrame")
        btns.pack(fill="x", pady=(0, p(10)))
        ttk.Button(btns, text="Open channel ↗", style="Accent.TButton",
                   command=self.open_channel).pack(side="left")
        ttk.Button(btns, text="✉  Write email", style="Accent.TButton",
                   command=self.write_email).pack(side="left", padx=(p(8), 0))
        for label, cmd in (("✓ OK [V]", self.email_ok), ("✗ Wrong [X]", self.email_wrong),
                           ("Reveal email", self.reveal_email), ("Copy email", self.copy_email),
                           ("Copy draft", self.copy_draft), ("Contacted ✓/✗", self.toggle_contacted)):
            ttk.Button(btns, text=label, style="Ghost.TButton", command=cmd).pack(side="left", padx=(p(8), 0))
        self.t_detail = self._text(df, height=9, state="disabled", bg=SURFACE, highlightthickness=0,
                                   padx=p(2), pady=p(2), cursor="arrow")
        ys2 = ttk.Scrollbar(df, orient="vertical", command=self.t_detail.yview)
        self.t_detail.configure(yscrollcommand=ys2.set)
        ys2.pack(side="right", fill="y")
        self.t_detail.pack(fill="both", expand=True)
        t = self.t_detail
        t.tag_configure("title", font=(UI_BLACK, 15), foreground=TEXT)
        t.tag_configure("score", font=(UI_BLACK, 15), foreground=RED_TEXT)
        t.tag_configure("h", font=(UI_SEMI, 9), foreground=MUTED, spacing1=p(12), spacing3=p(4))
        t.tag_configure("muted", foreground=MUTED)
        t.tag_configure("barfull", foreground=RED, font=(MONO, 10))
        t.tag_configure("barempty", foreground="#33333b", font=(MONO, 10))
        t.tag_configure("barneg", foreground="#55555f", font=(MONO, 10))
        t.tag_configure("mono", font=(MONO, 10), foreground=MUTED)
        t.tag_configure("link", foreground=RED_TEXT, underline=True)
        t.tag_configure("good", foreground=GREEN)
        t.tag_configure("warn", foreground=RED_TEXT)
        t.tag_bind("link", "<Button-1>", self._click_link)
        t.tag_bind("link", "<Enter>", lambda e: t.config(cursor="hand2"))
        t.tag_bind("link", "<Leave>", lambda e: t.config(cursor="arrow"))
        self._detail_placeholder()
        pw.add(o, weight=2)

    def _detail_placeholder(self):
        t = self.t_detail
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "Select a creator in the table to see why they got that score, "
                        "their contact details and latest videos.", "muted")
        t.config(state="disabled")

    def _render_results(self):
        self.tree.delete(*self.tree.get_children())
        self.by_iid.clear()
        top_n = to_int(self.v_top.get(), 10)
        times = self.store.contacted_times()
        status = {r["channel_id"]: r["status"] for r in self.store.contacted_rows()}
        rows = self._sorted_rows(filtered=True)
        default_order = self.sort_col is None and self.v_sort.get() == SORTS[0]
        for i, c in enumerate(rows, 1):
            tags = ["top" if i <= top_n and default_order else ("odd" if i % 2 else "even")]
            if c.channel_id in times:
                tags.append("done")
            d = c.days_since_upload
            iid = self.tree.insert("", "end", tags=tags, values=(
                f"{i:02d}", ("  ✚ " if c.added_manually else "  ") + c.title, f"{100 * c.chance:.0f}%",
                scout.fmt_num(c.exp_views) if c.pred_views else "—",
                scout.fmt_num(c.pred_views) if c.pred_views else "—",
                scout.fmt_num(c.subs), "✓" if c.key_videos else "",
                ", ".join(sorted(c.matched_games)) or "—",
                "today" if d == 0 else f"{d}d ago" if d is not None else "—",
                c.emails[0]["email"] if c.emails else "—",
                c.emails[0].get("source", "YouTube") if c.emails else "",
                CONF_LABEL.get(c.emails[0].get("conf", ""), "") if c.emails else "",
                _contacted_cell(status.get(c.channel_id), times.get(c.channel_id)), f"{c.total:.0f}",
            ))
            self.by_iid[iid] = c
        email_mode = self.v_efilter.get() != EMAIL_FILTERS[0]
        self.tree["displaycolumns"] = (
            ("rank", "channel", "email", "check", "source", "subs", "chance", "exp", "games", "done")
            if email_mode else "#all")
        q = self.v_q.get().strip()
        exact = q and any(self._exact(c, q) for c in rows)
        if q and self.results and not exact:
            if q != getattr(self, "_why_for", None):
                self._why_for = q
                self.why_text.config(text=(f"No creator in these results matches “{q}”." if not rows else
                                           f"No exact match for “{q}” — only similar names."), fg=TEXT)
            if not self.why_bar.winfo_ismapped():
                self.why_bar.pack(fill="x", pady=(self.px(8), 0), after=self._srow)
        elif self.why_bar.winfo_ismapped():
            self.why_bar.pack_forget()
            self._why_for = None
        if self.results:
            n = len(self.results)
            self.l_count.config(text=f"Showing {len(rows)} of {n}" if len(rows) != n else f"{n} creators")
        else:
            self.l_count.config(text="")

    # ---------------------------------------------- why isn't X here? / add it
    def _find_in_run(self, kind, val):
        allc = getattr(self, "last_stats", {}).get("all", {})
        if kind == "id":
            return allc.get(val)
        if kind == "handle":
            return next((c for c in allc.values() if lookup.norm_handle(c.custom_url) == val), None)
        if kind in ("name", "custom", "user"):
            qn = lookup.norm_name(val)
            return next((c for c in allc.values()
                         if lookup.norm_name(c.title) == qn or lookup.norm_handle(c.custom_url) == qn.replace(" ", "")),
                        None)
        return None

    def _resolve_async(self, then):
        """Find the channel for whatever is in the search box (offline first, then YouTube)."""
        q = self.v_q.get().strip()
        kind, val = lookup.parse_ref(q)
        if kind == "empty":
            kind, val = "name", q
        c = self._find_in_run(kind, val)
        if c is not None:
            then(c.channel_id, c.title, c.subs, None)
            return
        key = self.v_key.get().strip()
        if not key:
            then(None, None, None, "Add your API key in Settings first.")
            return
        cost = "100 units" if kind in ("name", "custom") else "1–2 units"
        self.why_text.config(text=f"Asking YouTube who “{q}” is ({cost})…", fg=MUTED)
        client = YouTubeClient(key, self.store)

        def work():
            try:
                found = client.resolve(kind, val)
                res = (found[0]["channel_id"], found[0]["title"], found[0]["subs"], None) if found else \
                    (None, None, None, "YouTube doesn't know that channel — check the link or spelling.")
            except Exception as e:
                res = (None, None, None, f"Couldn't ask YouTube: {e}")
            self.q.put(("call", lambda: then(*res), None))
        threading.Thread(target=work, daemon=True).start()

    def why_missing(self):
        st = getattr(self, "last_settings", None)
        if st is None:
            return

        def show(cid, title, subs, err):
            if err:
                self.why_text.config(text=err, fg=RED_TEXT)
                return
            stats = self.last_stats
            c = stats["all"].get(cid)
            if c is not None and c.subs is None and subs is not None:
                c.subs = subs
            reason = scout.explain(c, st, excluded=cid in stats.get("excluded", ()), queries=stats.get("queries", []))
            size = f" ({scout.fmt_num(subs if c is None else c.subs)} subscribers)" if (c or subs is not None) else ""
            self.why_text.config(text=f"{title}{size}: {reason}", fg=TEXT)
        self._resolve_async(show)

    def add_to_results(self):
        st = getattr(self, "last_settings", None)
        key = self.v_key.get().strip()
        if st is None or not key:
            return
        self.b_add.config(state="disabled")

        def go(cid, title, subs, err):
            if err:
                self.b_add.config(state="normal")
                self.why_text.config(text=err, fg=RED_TEXT)
                return
            self.why_text.config(text=f"Analysing {title}…", fg=MUTED)
            client = YouTubeClient(key, self.store)

            def work():
                try:
                    c = scout.add_channel(cid, client, st, self.results, self.last_stats["all"],
                                          embedder_loader=self._load_embedder)
                    self._apply_manual([c])
                    c.reasons = scout.reasons(c)
                    self.q.put(("call", lambda: self._added(c), None))
                except Exception as e:
                    msg = str(e)
                    self.q.put(("call", lambda: (self.b_add.config(state="normal"),
                                                 self.why_text.config(text=f"Couldn't add it: {msg}", fg=RED_TEXT)),
                                None))
            threading.Thread(target=work, daemon=True).start()
        self._resolve_async(go)

    def _added(self, c):
        self.b_add.config(state="normal")
        self.v_q.set("")
        self.v_efilter.set(EMAIL_FILTERS[0])
        self._render_results()
        self._reselect(c)
        pos = self._sorted_rows(filtered=True).index(c) + 1
        self.kpi["ranked"].config(text=f"{len(self.results):,}")
        self._toast(f"Added {c.title} — #{pos} of {len(self.results)}")

    def _exact(self, c, q: str) -> bool:
        kind, val = lookup.parse_ref(q)
        if kind == "id":
            return c.channel_id == val
        if kind == "handle":
            return lookup.norm_handle(c.custom_url) == val
        qn = lookup.norm_name(q)
        return lookup.norm_name(c.title) == qn or lookup.norm_handle(c.custom_url) == qn.replace(" ", "")

    def _matches(self, c, q: str) -> bool:
        kind, val = lookup.parse_ref(q)
        if kind == "id":
            return c.channel_id == val
        if kind == "handle":
            return lookup.norm_handle(c.custom_url).startswith(val)
        hay = " ".join([c.title, c.custom_url, c.channel_id] + [e["email"] for e in c.emails]).lower()
        return all(word in hay for word in q.lower().split())

    def _sorted_rows(self, filtered: bool = False):
        rows = list(self.results)
        if filtered:
            ef = self.v_efilter.get()
            if ef == EMAIL_FILTERS[1]:
                rows = [c for c in rows if c.emails]
            elif ef == EMAIL_FILTERS[2]:
                rows = [c for c in rows if c.emails and c.emails[0].get("conf") != "verified"]
            elif ef == EMAIL_FILTERS[3]:
                rows = [c for c in rows if c.emails and c.emails[0].get("conf") == "verified"]
            elif ef == EMAIL_FILTERS[4]:
                rows = [c for c in rows if not c.emails]
            q = self.v_q.get().strip()
            if q:
                rows = [c for c in rows if self._matches(c, q)]
        if self.sort_col:
            times = self.store.contacted_times()
            keys = {
                "rank": lambda c: (c.exp_views, c.chance), "channel": lambda c: lookup.natural_key(c.title),
                "chance": lambda c: c.chance, "exp": lambda c: c.exp_views, "pred": lambda c: c.pred_views or -1,
                "subs": lambda c: -1 if c.subs is None else c.subs, "keys": lambda c: c.key_videos,
                "games": lambda c: len(c.matched_games),
                "last": lambda c: 10 ** 6 if c.days_since_upload is None else c.days_since_upload,
                "email": lambda c: c.emails[0]["email"] if c.emails else "~",
                "source": lambda c: c.emails[0].get("source", "") if c.emails else "~",
                "done": lambda c: times.get(c.channel_id, 0), "match": lambda c: c.total,
                "check": lambda c: (CONF_ORDER.get(c.emails[0].get("conf"), 8) if c.emails else 9),
            }
            return sorted(rows, key=keys[self.sort_col], reverse=self.sort_desc)
        mode = self.v_sort.get()
        if mode == SORTS[3]:
            return sorted(rows, key=lambda c: lookup.natural_key(c.title))
        if mode in (SORTS[4], SORTS[5]):
            return sorted(rows, key=lambda c: -1 if c.subs is None else c.subs, reverse=mode == SORTS[4])
        key = {SORTS[1]: lambda c: (c.chance, c.exp_views), SORTS[2]: lambda c: (c.total, c.chance)}.get(
            mode, lambda c: (c.exp_views, c.chance))
        return sorted(rows, key=key, reverse=True)

    def _click_heading(self, col: str):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_desc = col not in TEXT_COLS and col != "rank"
            if col == "rank":
                self.sort_desc = True
        self._set_sort_col(col)

    def _set_sort_col(self, col):
        self.sort_col = col
        for c, h in HEADS.items():
            arrow = ("  ▼" if self.sort_desc else "  ▲") if c == col else ""
            self.tree.heading(c, text=h + arrow)
        self._render_results()

    def _selected(self):
        sel = self.tree.selection()
        return self.by_iid.get(sel[0]) if sel else None

    def _bar(self, t, label: str, val: float):
        n = 20
        full = int(round(max(0.0, min(1.0, val)) * n))
        t.insert("end", f"  {label:<16}", "mono")
        t.insert("end", "█" * full, "barfull")
        t.insert("end", "█" * (n - full), "barempty")
        t.insert("end", f"  {val * 100:3.0f}\n", "mono")

    def _show_detail(self):
        c = self._selected()
        if not c:
            return
        t = self.t_detail
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", c.title + "   ", "title")
        t.insert("end", f"{100 * c.chance:.0f}%", "score")
        t.insert("end", " coverage chance", "muted")
        if c.pred_views:
            t.insert("end", "   ·   ", "muted")
            t.insert("end", f"~{scout.fmt_num(c.exp_views)}", "score")
            t.insert("end", f" expected views per pitch  (~{scout.fmt_num(c.pred_views)} if they cover it)", "muted")
        t.insert("end", "\n")
        t.insert("end", c.url, "link")
        t.insert("end", f"     match score {c.total:.0f}/100\n", "muted")
        info = next((r for r in self.store.contacted_rows() if r["channel_id"] == c.channel_id), None)
        if info:
            status = f" · {info['status']}" if info["status"] != "Contacted" else ""
            t.insert("end", f"✓ Contacted on {long_date(info['ts'])}{status}" +
                     (f" · {info['note']}" if info["note"] else "") + "\n", "good")
            if info["video_url"]:
                t.insert("end", f"▶ Their video: {info['video_title']}  ", "good")
                t.insert("end", info["video_url"], "link")
                t.insert("end", "\n")

        t.insert("end", "EMAILS  (V = email OK · X = wrong email · O = open where it was found)\n", "h")
        if c.emails:
            for e in c.emails:
                conf = e.get("conf", "")
                t.insert("end", "  " + e["email"] + "   ")
                t.insert("end", CONF_LABEL.get(conf, conf), "good" if conf in ("verified", "high") else
                         "warn" if conf in ("low", "shared") else "mono")
                t.insert("end", f"  · {e.get('source', 'YouTube')}" + (f" · {e['why']}" if e.get("why") else "") + "\n",
                         "muted")
                if e.get("url"):
                    t.insert("end", "      found on ", "muted")
                    t.insert("end", e["url"], "link")
                    t.insert("end", "\n")
        else:
            t.insert("end", "  None in descriptions. YouTube hides the About-tab email behind a "
                            "CAPTCHA — check it manually on the channel, or reach out on Twitter/Discord.\n", "muted")
        t.insert("end", "WHAT DRIVES THE PREDICTION  (pushes the chance up ▸ red · down ◂ grey)\n", "h")
        top = sorted(c.contrib.items(), key=lambda kv: -abs(kv[1]))
        scale = max([abs(v) for _, v in top] + [0.5])
        for f, v in top:
            if abs(v) < 0.02:
                continue
            n = int(round(abs(v) / scale * 18))
            t.insert("end", f"  {cov_model.LABELS[f]:<28}", "mono")
            t.insert("end", "█" * max(1, n), "barfull" if v > 0 else "barneg")
            t.insert("end", f"  {'+' if v > 0 else '−'}{abs(v):.2f}\n", "mono")

        t.insert("end", "WHY\n", "h")
        for r in c.reasons:
            t.insert("end", "  —  " + r + "\n")

        if c.links:
            t.insert("end", "LINKS\n", "h")
            for l in c.links:
                t.insert("end", f"  {l['kind']:<10}", "mono")
                t.insert("end", l["url"], "link")
                t.insert("end", "\n")
        if c.recent:
            t.insert("end", "LATEST VIDEOS\n", "h")
            for v in c.recent[:8]:
                t.insert("end", "  ▸ " + v["title"] + "\n")
        if c.description:
            t.insert("end", "CHANNEL DESCRIPTION\n", "h")
            t.insert("end", c.description[:1200] + "\n", "muted")
        t.config(state="disabled")
        t.yview_moveto(0)

    def _click_link(self, event):
        idx = self.t_detail.index(f"@{event.x},{event.y}")
        rng = self.t_detail.tag_prevrange("link", idx + "+1c")
        if rng:
            webbrowser.open(self.t_detail.get(*rng).strip())

    def open_channel(self):
        c = self._selected()
        if c:
            webbrowser.open(c.url)

    def _apply_manual(self, cands):
        """Your decisions win over anything automatic: emails you added, approved or rejected."""
        manual = self.store.manual_emails()
        reviews = self.store.email_reviews()
        for c in cands:
            emails = [e for e in c.emails if e.get("source") != "manual"]
            m = manual.get(c.channel_id)
            if m:
                emails = [{"email": m, "business": True, "source": "manual", "url": "",
                           "why": "added by you"}] + [e for e in emails if e["email"] != m]
            kept = []
            for e in emails:
                st = reviews.get((c.channel_id, e["email"]))
                if st == "bad":
                    continue
                if st == "ok" or e.get("source") == "manual":
                    e = {**e, "conf": "verified"}
                kept.append(e)
            c.emails = sorted(kept, key=lambda e: CONF_ORDER.get(e.get("conf"), 4))

    def email_ok(self):
        self._review_email("ok")

    def email_wrong(self):
        self._review_email("bad")

    def _review_email(self, status: str):
        c = self._selected()
        if not c or not c.emails:
            if c:
                self._toast("No email to review for this creator")
            return
        email = c.emails[0]["email"]
        sel = self.tree.selection()
        idx = self.tree.index(sel[0]) if sel else 0
        self.store.set_email_review(c.channel_id, email, status)
        self._apply_manual([c])
        c.reasons = scout.reasons(c)
        self._render_results()
        if not self._reselect(c, quiet=True):          # row left the current filter -> go to the next one
            kids = self.tree.get_children()
            if kids:
                nxt = kids[min(idx, len(kids) - 1)]
                self.tree.selection_set(nxt)
                self.tree.see(nxt)
                self.tree.focus(nxt)
        self.tree.focus_set()
        self._toast(f"{email} marked OK" if status == "ok" else f"{email} removed")

    def open_email_source(self):
        c = self._selected()
        if c and c.emails and c.emails[0].get("url"):
            webbrowser.open(c.emails[0]["url"])

    def reveal_email(self):
        c = self._selected()
        if not c:
            messagebox.showinfo(APP, "Select a creator in the table first.")
            return
        webbrowser.open(f"https://www.youtube.com/channel/{c.channel_id}/about")
        current = next((e["email"] for e in c.emails if e.get("source") == "manual"), "")
        self._email_dialog(c, current)

    def _email_dialog(self, c, current: str):
        p = self.px
        d = tk.Toplevel(self, bg=SURFACE)
        d.title("Reveal email")
        d.transient(self)
        d.resizable(False, False)
        box = tk.Frame(d, bg=SURFACE, padx=p(24), pady=p(20))
        box.pack(fill="both", expand=True)
        tk.Label(box, text=f"Reveal email · {c.title}", bg=SURFACE, fg=TEXT,
                 font=(UI_BLACK, 14)).pack(anchor="w")
        tk.Label(box, bg=SURFACE, fg=MUTED, font=(UI, 10), justify="left", text=(
            "1.  In the browser tab that just opened, click “View email address”.\n"
            "2.  Solve the CAPTCHA (YouTube requires being signed in).\n"
            "3.  Copy the email and paste it below. It is saved for future runs."
        )).pack(anchor="w", pady=(p(8), p(14)))
        var = tk.StringVar(value=current)
        ent = ttk.Entry(box, textvariable=var, width=44, font=(MONO, 11))
        ent.pack(fill="x")
        ent.focus_set()
        err = tk.Label(box, text="", bg=SURFACE, fg=RED_TEXT, font=(UI, 9))
        err.pack(anchor="w", pady=(p(4), 0))

        def save(*_):
            val = var.get().strip().strip("<>").lower()
            if val and not EMAIL_RE.fullmatch(val):
                err.config(text="That doesn't look like an email address.")
                return
            self.store.set_manual_email(c.channel_id, val or None)
            self._apply_manual([c])
            c.reasons = scout.reasons(c)
            d.destroy()
            self._render_results()
            self._reselect(c)
            self._toast("Email saved" if val else "Email removed")

        btns = tk.Frame(box, bg=SURFACE)
        btns.pack(fill="x", pady=(p(12), 0))
        ttk.Button(btns, text="Save email", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(btns, text="Open channel again ↗", style="Ghost.TButton",
                   command=lambda: webbrowser.open(f"https://www.youtube.com/channel/{c.channel_id}/about")
                   ).pack(side="left", padx=p(8))
        if current:
            ttk.Button(btns, text="Remove", style="Ghost.TButton",
                       command=lambda: (var.set(""), save())).pack(side="left")
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=d.destroy).pack(side="right")
        ent.bind("<Return>", save)
        d.bind("<Escape>", lambda e: d.destroy())
        d.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - d.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - d.winfo_height()) // 3
        d.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._email_dlg = d
        try:
            d.grab_set()
        except tk.TclError:
            pass

    def _reselect(self, c, quiet: bool = False) -> bool:
        for iid, cc in self.by_iid.items():
            if cc is c:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self.tree.focus(iid)
                if not quiet:
                    self._show_detail()
                return True
        return False

    def copy_email(self):
        c = self._selected()
        if c and c.emails:
            self._clip(c.emails[0]["email"], "Email")
        elif c:
            messagebox.showinfo(APP, "This channel has no public email in its descriptions.")

    def copy_draft(self):
        c = self._selected()
        if c:
            cfg = self._collect_cfg()
            self._clip(scout.draft_email(c, cfg, cfg["template"]), "Draft")

    def write_email(self):
        """Open a pre-filled draft (Gmail, Outlook or the default mail app) — you send it yourself."""
        c = self._selected()
        if not c:
            return
        cfg = self._collect_cfg()
        draft = scout.draft_email(c, cfg, cfg["template"])
        current = c.emails[0]["email"] if c.emails else ""

        def go(email):
            if email != current:
                self.store.set_manual_email(c.channel_id, email)
                self._apply_manual([c])
            self._open_mail(email, draft)
            if c.channel_id not in self.store.contacted_ids() and \
                    messagebox.askyesno(APP, f"Mark {c.title} as contacted?"):
                self.store.set_contacted(c.channel_id, c.title, True, handle=lookup.norm_handle(c.custom_url),
                                         email=email, url=c.url)
                self._toast("Marked as contacted")
            self._render_results()
            self._render_contacts()
            self._reselect(c)
        self._ask_email_then(c.title, current, go)

    def toggle_contacted(self):
        c = self._selected()
        if not c:
            return
        now = c.channel_id not in self.store.contacted_ids()
        self.store.set_contacted(c.channel_id, c.title, now, handle=lookup.norm_handle(c.custom_url),
                                 email=c.emails[0]["email"] if c.emails else "", url=c.url)
        self._render_results()
        self._render_contacts()
        for iid, cc in self.by_iid.items():
            if cc is c:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                break
        self._toast("Marked as contacted" if now else "Marked as not contacted")

    def export(self, top_only: bool):
        if not self.results:
            messagebox.showinfo(APP, "There are no results to export yet.")
            return
        rows = self._sorted_rows()
        rows = rows[: to_int(self.v_top.get(), 10)] if top_only else rows
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"creators_{datetime.now():%Y%m%d_%H%M}.csv")
        if not path:
            return
        cfg = self._collect_cfg()
        done = self.store.contacted_ids()
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["rank", "channel", "url", "coverage_chance", "expected_views_per_pitch",
                        "predicted_views", "accepted_keys_videos", "similar_games_views_ratio",
                        "semantic_similarity", "keyword_similarity", "match_score", "content", "comparable_games_score",
                        "size", "engagement", "activity", "subscribers", "median_views",
                        "days_since_upload", "country", "comparable_games_played", "emails",
                        "email_found_on", "email_confidence", "email_reason", "email_found_url",
                        "links", "contacted", "why", "draft_email"])
            for i, c in enumerate(rows, 1):
                s = c.scores
                w.writerow([
                    i, c.title, c.url, round(c.chance, 3), int(c.exp_views) if c.pred_views else "",
                    int(c.pred_views) if c.pred_views else "", c.key_videos,
                    round(c.genre_ratio, 2) if c.genre_ratio else "", round(c.sem_sim, 3), round(c.kw_sim, 3),
                    c.total, round(s["content"], 3), round(s["coverage"], 3),
                    round(s["size"], 3), round(s["engagement"], 3), round(s["activity"], 3),
                    "" if c.subs is None else c.subs,
                    "" if c.median_views is None else int(c.median_views),
                    c.days_since_upload, c.country, "; ".join(sorted(c.matched_games)),
                    "; ".join(e["email"] for e in c.emails),
                    c.emails[0].get("source", "YouTube") if c.emails else "",
                    c.emails[0].get("conf", "") if c.emails else "",
                    c.emails[0].get("why", "") if c.emails else "",
                    c.emails[0].get("url", "") if c.emails else "",
                    " ".join(l["url"] for l in c.links),
                    "yes" if c.channel_id in done else "",
                    " | ".join(c.reasons),
                    scout.draft_email(c, cfg, cfg["template"]),
                ])
        self._toast(f"Exported {len(rows)} rows")
        self._log(f"Exported {len(rows)} rows to {path}")


def _contacted_cell(status: str | None, ts: float | None) -> str:
    """'12 Oct' while waiting for an answer, then the outreach status (e.g. '▶ Video published')."""
    if not ts:
        return ""
    if status and status != "Contacted":
        return f"{outreach.STATUS_ICON.get(status, '')} {status}"
    return short_date(ts)

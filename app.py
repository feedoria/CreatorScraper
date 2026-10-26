"""CreatorScout — find and rank YouTube creators who fit your game.

Run:  python app.py      (or the built CreatorScout.exe)
"""
import csv
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import embed
import lookup
import enrich
import model as cov_model
import scout
import steam
from contacts import CONF_ORDER, EMAIL_RE
from storage import Store, data_dir, load_settings, save_settings
from youtube_api import ApiError, YouTubeClient

APP = "CreatorScout"
SORTS = ["Expected views per pitch", "Coverage chance", "Match score", "Name (A–Z)",
         "Subscribers (largest first)", "Subscribers (smallest first)"]
HEADS = {"rank": "#", "channel": "CHANNEL", "chance": "CHANCE", "exp": "EXP. VIEWS", "pred": "PRED. VIEWS",
         "subs": "SUBS", "keys": "KEYS", "games": "SIMILAR GAMES", "last": "LAST UPLOAD", "email": "EMAIL",
         "source": "FOUND ON", "check": "EMAIL CHECK", "done": "CONTACTED", "match": "MATCH"}
TEXT_COLS = ("channel", "email", "source", "check")
EMAIL_FILTERS = ["All creators", "With email", "Emails to check", "Verified emails", "No email yet"]
CONF_LABEL = {"verified": "✓ verified", "high": "High", "medium": "Medium", "low": "Low · check"}
KEY_HELP = "https://console.cloud.google.com/apis/library/youtube.googleapis.com"

if sys.platform == "darwin":
    UI, UI_SEMI, UI_BLACK, MONO = "Helvetica Neue", "Helvetica Neue", "Helvetica Neue", "Menlo"
elif sys.platform == "win32":
    UI, UI_SEMI, UI_BLACK, MONO = "Segoe UI", "Segoe UI Semibold", "Segoe UI Black", "Consolas"
else:
    UI, UI_SEMI, UI_BLACK, MONO = "DejaVu Sans", "DejaVu Sans", "DejaVu Sans", "DejaVu Sans Mono"

# ---------------------------------------------------------------- palette
BG = "#0b0b0d"        # window
SIDEBAR = "#060607"
SURFACE = "#141418"   # cards
SURFACE2 = "#1c1c22"  # inputs, table header
BORDER = "#2a2a32"
TEXT = "#ededf2"
MUTED = "#8d8d99"
FAINT = "#5c5c66"
RED = "#e5202e"
RED_HOVER = "#ff3a47"
RED_PRESS = "#b5121e"
RED_TINT = "#2a0f13"  # top-N rows
RED_TEXT = "#ff5c68"
GREEN = "#3ddc84"

EXAMPLE = {
    "game_name": "My Digging Game",
    "hook": "digging / mining",
    "game_description": (
        "A cozy-but-tense digging simulator: dig deep underground with shovels and drills, "
        "collect ores and gems, sell them at the shop, upgrade your tools and lay mine cart "
        "tracks while avoiding cave-ins and moles."
    ),
    "comparable_games": "Hydroneer\nSteamWorld Dig\nDome Keeper\nDeep Rock Galactic\n"
                        "Mining Mechanics\nA Game About Digging A Hole\nPowerWash Simulator",
    "keywords": "digging simulator\nmining simulator indie",
}


def resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP)
        self.configure(bg=BG)
        self.k = max(1.0, self.winfo_fpixels("1i") / 96.0)  # DPI scale for pixel sizes
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(self.px(1240), int(sw * 0.92)), min(self.px(800), int(sh * 0.88))
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(min(self.px(1000), w), min(self.px(640), h))
        self._set_icon()

        self.store = Store()
        self.cfg = {**EXAMPLE, **load_settings()}
        self.results: list[scout.Candidate] = []
        self.sort_col: str | None = None
        self.sort_desc = True
        self.by_iid: dict[str, scout.Candidate] = {}
        self.q: queue.Queue = queue.Queue()
        self.worker = None
        self.cancel = threading.Event()

        self._style()
        self._layout()
        self._build_search_page()
        self._build_results_page()
        self._build_contacts_page()
        self._build_template_page()
        self._build_settings_page()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._poll)
        self.show("settings" if not self.cfg.get("api_key") else "search")
        self._refresh_key_status()
        self.after(50, self._dark_titlebar)

    # --------------------------------------------------------------- helpers
    def px(self, n: float) -> int:
        return int(round(n * self.k))

    def _set_icon(self):
        try:
            self._icon = tk.PhotoImage(file=resource("assets/icon_256.png"))
            self.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        if sys.platform == "win32":
            try:
                self.iconbitmap(resource("assets/icon.ico"))
            except tk.TclError:
                pass

    def _dark_titlebar(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                    break
            color = ctypes.c_int(0x000D0B0B)  # caption color BGR (Win11)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), 4)
        except Exception:
            pass

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        p = self.px
        s.configure(".", background=BG, foreground=TEXT, font=(UI, 10), bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=SURFACE2,
                    focuscolor=RED, selectbackground=RED, selectforeground="white",
                    fieldbackground=SURFACE2, insertcolor=TEXT)
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=SURFACE)
        s.configure("Side.TFrame", background=SIDEBAR)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
        s.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=(UI, 9))
        s.configure("Muted.TLabel", background=BG, foreground=MUTED)
        s.configure("H1.TLabel", background=BG, foreground=TEXT, font=(UI_BLACK, 22))
        s.configure("Sub.TLabel", background=BG, foreground=MUTED, font=(UI, 10))
        s.configure("Field.TLabel", background=SURFACE, foreground=MUTED, font=(UI_SEMI, 9))
        s.configure("CardTitle.TLabel", background=SURFACE, foreground=TEXT, font=(UI_SEMI, 11))
        s.configure("KpiNum.TLabel", background=SURFACE, foreground=TEXT, font=(UI_BLACK, 20))
        s.configure("KpiRed.TLabel", background=SURFACE, foreground=RED_TEXT, font=(UI_BLACK, 20))
        s.configure("KpiLbl.TLabel", background=SURFACE, foreground=MUTED, font=(UI_SEMI, 8))

        # buttons
        s.configure("Accent.TButton", background=RED, foreground="white", font=(UI_SEMI, 10),
                    borderwidth=0, focusthickness=0, padding=(p(18), p(9)), relief="flat")
        s.map("Accent.TButton",
              background=[("disabled", "#3a1a1d"), ("pressed", RED_PRESS), ("active", RED_HOVER)],
              foreground=[("disabled", "#8a5a5e")])
        s.configure("Ghost.TButton", background=SURFACE2, foreground=TEXT, font=(UI_SEMI, 9),
                    borderwidth=1, bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2,
                    focusthickness=0, padding=(p(14), p(7)), relief="flat")
        s.map("Ghost.TButton",
              background=[("disabled", SURFACE), ("pressed", BORDER), ("active", "#26262e")],
              foreground=[("disabled", FAINT)],
              bordercolor=[("active", RED)])

        # inputs
        for w in ("TEntry", "TSpinbox"):
            s.configure(w, fieldbackground=SURFACE2, foreground=TEXT, insertcolor=TEXT,
                        bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2,
                        padding=(p(8), p(6)), arrowcolor=MUTED, background=SURFACE2)
            s.map(w, bordercolor=[("focus", RED)], lightcolor=[("focus", RED)],
                  darkcolor=[("focus", RED)])
        for name, bg in (("TCheckbutton", BG), ("Card.TCheckbutton", SURFACE)):
            s.configure(name, background=bg, foreground=TEXT, indicatorbackground=SURFACE2,
                        indicatorforeground="white", indicatormargin=(0, 0, p(8), 0),
                        focusthickness=0, font=(UI, 10))
            s.map(name, background=[("active", bg)],
                  indicatorbackground=[("selected", RED), ("active", BORDER)])

        s.configure("TCombobox", fieldbackground=SURFACE2, background=SURFACE2, foreground=TEXT,
                    arrowcolor=MUTED, bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2,
                    padding=(p(8), p(5)), selectbackground=SURFACE2, selectforeground=TEXT)
        s.map("TCombobox", fieldbackground=[("readonly", SURFACE2)], foreground=[("readonly", TEXT)],
              bordercolor=[("focus", RED)], selectbackground=[("readonly", SURFACE2)],
              selectforeground=[("readonly", TEXT)])
        self.option_add("*TCombobox*Listbox.background", SURFACE2)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", RED)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*TCombobox*Listbox.font", (UI, 10))
        s.configure("Red.Horizontal.TProgressbar", troughcolor=SURFACE2, background=RED,
                    bordercolor=SURFACE2, lightcolor=RED, darkcolor=RED, thickness=p(6))

        # table
        s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                    rowheight=p(34), borderwidth=0, relief="flat", font=(UI, 10))
        s.map("Treeview", background=[("selected", RED)], foreground=[("selected", "white")])
        s.configure("Treeview.Heading", background=SURFACE2, foreground=MUTED, relief="flat",
                    font=(UI_SEMI, 9), borderwidth=0, padding=(p(8), p(8)))
        s.map("Treeview.Heading", background=[("active", BORDER)], foreground=[("active", TEXT)])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=SURFACE2, troughcolor=SURFACE,
                        bordercolor=SURFACE, lightcolor=SURFACE2, darkcolor=SURFACE2,
                        arrowcolor=MUTED, gripcount=0, arrowsize=p(12))
            s.map(f"{orient}.TScrollbar", background=[("active", BORDER)])
        s.configure("TPanedwindow", background=BG)
        s.configure("Sash", sashthickness=p(10), background=BG, gripcount=0,
                    lightcolor=BG, darkcolor=BG, bordercolor=BG)

    def _text(self, parent, **kw) -> tk.Text:
        opts = dict(bg=SURFACE2, fg=TEXT, insertbackground=RED, selectbackground=RED,
                    selectforeground="white", relief="flat", borderwidth=0,
                    highlightthickness=1, highlightbackground=BORDER, highlightcolor=RED,
                    padx=self.px(10), pady=self.px(8), font=(UI, 10), wrap="word",
                    insertwidth=self.px(2))
        opts.update(kw)
        return tk.Text(parent, **opts)

    def _card(self, parent, title: str | None = None, hint: str | None = None, pad=16):
        outer = tk.Frame(parent, bg=BORDER)  # 1px border
        inner = ttk.Frame(outer, style="Card.TFrame", padding=self.px(pad))
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            head = ttk.Frame(inner, style="Card.TFrame")
            head.pack(fill="x", pady=(0, self.px(8)))
            ttk.Label(head, text=title, style="CardTitle.TLabel").pack(side="left")
            if hint:
                ttk.Label(head, text=hint, style="CardMuted.TLabel").pack(side="left", padx=self.px(10))
        return outer, inner

    # ---------------------------------------------------------------- layout
    def _layout(self):
        side = ttk.Frame(self, style="Side.TFrame", width=self.px(230))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(self, bg=BORDER, width=1).pack(side="left", fill="y")

        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=self.px(22), pady=(self.px(26), self.px(4)))
        try:
            self._logo = tk.PhotoImage(file=resource("assets/icon_256.png")).subsample(
                max(1, int(round(256 / self.px(36)))))
            tk.Label(brand, image=self._logo, bg=SIDEBAR).pack(side="left", padx=(0, self.px(10)))
        except tk.TclError:
            pass
        words = tk.Frame(brand, bg=SIDEBAR)
        words.pack(side="left")
        line = tk.Frame(words, bg=SIDEBAR)
        line.pack(anchor="w")
        tk.Label(line, text="CREATOR", bg=SIDEBAR, fg=TEXT, font=(UI_BLACK, 13)).pack(side="left")
        tk.Label(line, text="SCOUT", bg=SIDEBAR, fg=RED, font=(UI_BLACK, 13)).pack(side="left")
        tk.Label(words, text="creator finder for games", bg=SIDEBAR, fg=FAINT,
                 font=(UI, 8)).pack(anchor="w")

        tk.Frame(side, bg=SIDEBAR, height=self.px(26)).pack(fill="x")
        tk.Label(side, text="MENU", bg=SIDEBAR, fg=FAINT, font=(UI_SEMI, 8)).pack(
            anchor="w", padx=self.px(24), pady=(0, self.px(6)))

        self.nav: dict[str, tuple] = {}
        for key, num, label in (("search", "01", "Your game"), ("results", "02", "Results"),
                                ("contacts", "03", "Contacts"), ("template", "04", "Email template"),
                                ("settings", "05", "Settings")):
            row = tk.Frame(side, bg=SIDEBAR, cursor="hand2")
            row.pack(fill="x", pady=1)
            bar = tk.Frame(row, bg=SIDEBAR, width=self.px(3))
            bar.pack(side="left", fill="y")
            n = tk.Label(row, text=num, bg=SIDEBAR, fg=FAINT, font=(MONO, 9))
            n.pack(side="left", padx=(self.px(20), self.px(10)), pady=self.px(11))
            t = tk.Label(row, text=label, bg=SIDEBAR, fg=MUTED, font=(UI_SEMI, 10))
            t.pack(side="left")
            badge = tk.Label(row, text="", bg=SIDEBAR, fg="white", font=(UI_SEMI, 8))
            badge.pack(side="right", padx=self.px(16))
            for w in (row, n, t, badge):
                w.bind("<Button-1>", lambda e, k=key: self.show(k))
                w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self.nav[key] = (row, bar, n, t, badge)

        foot = tk.Frame(side, bg=SIDEBAR)
        foot.pack(side="bottom", fill="x", padx=self.px(22), pady=self.px(20))
        self.l_key_status = tk.Label(foot, text="", bg=SIDEBAR, fg=MUTED, font=(UI, 9),
                                     anchor="w", justify="left")
        self.l_key_status.pack(fill="x")
        self.l_units = tk.Label(foot, text="", bg=SIDEBAR, fg=FAINT, font=(UI, 8),
                                anchor="w", justify="left")
        self.l_units.pack(fill="x", pady=(self.px(4), 0))

        self.content = ttk.Frame(self)
        self.content.pack(side="left", fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.pages: dict[str, ttk.Frame] = {}
        self.current = None

    def _page(self, key: str, title: str, subtitle: str) -> ttk.Frame:
        f = ttk.Frame(self.content, padding=(self.px(32), self.px(26), self.px(32), self.px(20)))
        f.grid(row=0, column=0, sticky="nsew")
        ttk.Label(f, text=title, style="H1.TLabel").pack(anchor="w")
        sub = ttk.Label(f, text=subtitle, style="Sub.TLabel")
        sub.pack(anchor="w", pady=(self.px(2), self.px(18)))
        f.subtitle = sub
        self.pages[key] = f
        return f

    def show(self, key: str):
        self.current = key
        self.pages[key].tkraise()
        for k, (row, bar, n, t, badge) in self.nav.items():
            on = k == key
            bg = "#151518" if on else SIDEBAR
            for w in (row, n, t, badge):
                w.configure(bg=bg)
            bar.configure(bg=RED if on else SIDEBAR)
            n.configure(fg=RED if on else FAINT)
            t.configure(fg=TEXT if on else MUTED)
            badge.configure(bg=RED if badge.cget("text") else bg)

    def _nav_hover(self, key: str, inside: bool):
        if key == self.current:
            return
        row, bar, n, t, badge = self.nav[key]
        bg = "#101013" if inside else SIDEBAR
        for w in (row, n, t):
            w.configure(bg=bg)
        if not badge.cget("text"):
            badge.configure(bg=bg)
        t.configure(fg=TEXT if inside else MUTED)

    def _refresh_key_status(self):
        if self.v_key.get().strip():
            self.l_key_status.configure(text="●  API key set", fg=GREEN)
        else:
            self.l_key_status.configure(text="●  API key missing", fg=RED_TEXT)

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
                                for k in _lines(self.cfg.get("keywords", ""))]
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
        games = _lines(self.t_games.get("1.0", "end"))
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
        n = len(_lines(self.t_games.get("1.0", "end"))) + len(self._search_keywords())
        pages = _int(self.v_pages.get(), 1)
        deep = _int(self.v_deep.get(), 150)
        vids = max(10, min(500, _int(self.v_videos.get(), 100)))
        units = n * pages * 100 + deep * 2 * -(-vids // 50) + 10
        warn = "  ⚠ over the daily quota" if units > 10_000 else ""
        self.l_cost.config(text=f"Estimated cost ≈ {units:,} / 10,000 units today{warn}  ·  repeat runs are free from cache")

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
        for label, cmd in (("Email OK ✓  [V]", self.email_ok), ("Wrong email ✗  [X]", self.email_wrong),
                           ("Reveal email", self.reveal_email), ("Copy email", self.copy_email),
                           ("Copy draft", self.copy_draft),
                           ("Contacted ✓ / ✗", self.toggle_contacted)):
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

    # ---------------------------------------------------------- contacts page
    def _build_contacts_page(self):
        f = self._page("contacts", "Contacts",
                       "Everyone you've marked as contacted — and a quick check for creators you find on YouTube.")
        p = self.px
        o, c = self._card(f, "Have I contacted them?",
                          "paste a channel link, a video or Shorts link, an @handle or a channel name")
        o.pack(fill="x")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x")
        self.v_check = tk.StringVar()
        ent = ttk.Entry(row, textvariable=self.v_check, font=(UI, 11))
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self.check_creator())
        ttk.Button(row, text="Check", style="Accent.TButton", command=self.check_creator).pack(side="left", padx=(p(10), 0))
        ttk.Button(row, text="Paste & check", style="Ghost.TButton", command=self._paste_check).pack(side="left", padx=(p(8), 0))
        self.check_box = ttk.Frame(c, style="Card.TFrame")
        self.check_box.pack(fill="x", pady=(p(10), 0))

        tools = ttk.Frame(f)
        tools.pack(fill="x", pady=(p(16), p(10)))
        ttk.Label(tools, text="⌕  Search", style="Muted.TLabel").pack(side="left")
        self.v_cq = tk.StringVar()
        ttk.Entry(tools, textvariable=self.v_cq, width=32).pack(side="left", padx=p(8))
        self.v_cq.trace_add("write", lambda *a: self._render_contacts())
        self.l_ccount = ttk.Label(tools, text="", style="Muted.TLabel")
        self.l_ccount.pack(side="left", padx=p(10))
        ttk.Button(tools, text="Export CSV", style="Ghost.TButton", command=self.export_contacts).pack(side="right")
        ttk.Button(tools, text="Remove", style="Ghost.TButton", command=self._remove_contact).pack(side="right", padx=p(8))
        ttk.Button(tools, text="Edit note", style="Ghost.TButton", command=self._edit_note).pack(side="right")
        ttk.Button(tools, text="Open channel ↗", style="Accent.TButton",
                   command=self._open_contact).pack(side="right", padx=p(8))

        o, tf = self._card(f, pad=0)
        o.pack(fill="both", expand=True)
        cols = ("channel", "handle", "email", "date", "note")
        heads = ("CHANNEL", "HANDLE", "EMAIL", "CONTACTED ON", "NOTE")
        widths = (230, 170, 230, 130, 360)
        self.ctree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for col, h, w in zip(cols, heads, widths):
            self.ctree.heading(col, text=h, anchor="w")
            self.ctree.column(col, width=p(w), minwidth=p(60), anchor="w")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.ctree.yview)
        self.ctree.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.ctree.pack(side="left", fill="both", expand=True)
        self.ctree.tag_configure("odd", background=SURFACE)
        self.ctree.tag_configure("even", background="#17171c")
        self.ctree.tag_configure("hit", background=RED_TINT)
        self.ctree.bind("<Double-1>", lambda e: self._open_contact())
        self._render_contacts()

    def _render_contacts(self, highlight: str | None = None):
        if not hasattr(self, "ctree"):
            return
        self.ctree.delete(*self.ctree.get_children())
        rows = self.store.contacted_rows()
        q = self.v_cq.get().strip().lower()
        shown = [r for r in rows if not q or q in " ".join(
            [r["title"], r["handle"], r["email"], r["note"], r["channel_id"]]).lower()]
        for i, r in enumerate(shown):
            tag = "hit" if r["channel_id"] == highlight else ("odd" if i % 2 else "even")
            self.ctree.insert("", "end", iid=r["channel_id"], tags=[tag], values=(
                "  " + r["title"], "@" + r["handle"] if r["handle"] else "", r["email"],
                _long_date(r["ts"]), r["note"]))
        self.l_ccount.config(text=f"{len(shown)} of {len(rows)} contacted" if q else f"{len(rows)} contacted")
        if highlight and self.ctree.exists(highlight):
            self.ctree.selection_set(highlight)
            self.ctree.see(highlight)

    def _contact_row(self):
        sel = self.ctree.selection()
        if not sel:
            messagebox.showinfo(APP, "Select someone in the list first.")
            return None
        return next((r for r in self.store.contacted_rows() if r["channel_id"] == sel[0]), None)

    def _open_contact(self):
        r = self._contact_row()
        if r:
            webbrowser.open(_channel_url(r))

    def _remove_contact(self):
        r = self._contact_row()
        if r and messagebox.askyesno(APP, f"Remove {r['title']} from your contacted list?"):
            self.store.set_contacted(r["channel_id"], r["title"], False)
            self._render_contacts()
            self._render_results()
            self._toast("Removed")

    def _edit_note(self):
        r = self._contact_row()
        if not r:
            return
        self._ask_text(f"Note · {r['title']}", "e.g. “sent key on 12 Oct, follow up next week”", r["note"],
                       lambda val: (self.store.set_contacted_note(r["channel_id"], val),
                                    self._render_contacts(highlight=r["channel_id"]), self._toast("Note saved")))

    def _ask_text(self, title: str, hint: str, initial: str, on_save):
        p = self.px
        d = tk.Toplevel(self, bg=SURFACE)
        d.title(title)
        d.transient(self)
        d.resizable(False, False)
        box = tk.Frame(d, bg=SURFACE, padx=p(24), pady=p(20))
        box.pack(fill="both", expand=True)
        tk.Label(box, text=title, bg=SURFACE, fg=TEXT, font=(UI_BLACK, 14)).pack(anchor="w")
        tk.Label(box, text=hint, bg=SURFACE, fg=MUTED, font=(UI, 10)).pack(anchor="w", pady=(p(6), p(10)))
        var = tk.StringVar(value=initial)
        ent = ttk.Entry(box, textvariable=var, width=52, font=(UI, 11))
        ent.pack(fill="x")
        ent.focus_set()

        def save(*_):
            on_save(var.get().strip())
            d.destroy()
        btns = tk.Frame(box, bg=SURFACE)
        btns.pack(fill="x", pady=(p(14), 0))
        ttk.Button(btns, text="Save", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=d.destroy).pack(side="right")
        ent.bind("<Return>", save)
        d.bind("<Escape>", lambda e: d.destroy())
        d.update_idletasks()
        d.geometry(f"+{self.winfo_rootx() + (self.winfo_width() - d.winfo_width()) // 2}"
                   f"+{self.winfo_rooty() + (self.winfo_height() - d.winfo_height()) // 3}")
        try:
            d.grab_set()
        except tk.TclError:
            pass

    def _paste_check(self):
        try:
            self.v_check.set(self.clipboard_get().strip())
        except tk.TclError:
            pass
        self.check_creator()

    def _check_show(self, lines):
        """lines: list of (text, colour, buttons[(label, command, accent)])"""
        for w in self.check_box.winfo_children():
            w.destroy()
        for text, colour, buttons in lines:
            row = ttk.Frame(self.check_box, style="Card.TFrame")
            row.pack(fill="x", pady=self.px(3))
            tk.Label(row, text=text, bg=SURFACE, fg=colour, font=(UI_SEMI, 11), anchor="w",
                     justify="left").pack(side="left")
            for label, cmd, accent in reversed(buttons):
                ttk.Button(row, text=label, style="Accent.TButton" if accent else "Ghost.TButton",
                           command=cmd).pack(side="right", padx=(self.px(8), 0))

    def check_creator(self):
        text = self.v_check.get().strip()
        kind, val = lookup.parse_ref(text)
        if kind == "empty":
            self._check_show([("Couldn't read that — paste a YouTube channel or video link, an @handle or a name.",
                               MUTED, [])])
            return
        rows = self.store.contacted_rows()
        hits = lookup.find_offline(rows, kind, val)
        if hits:
            self._check_show([self._contacted_line(r) for r in hits[:5]])
            self._render_contacts(highlight=hits[0]["channel_id"])
            return
        key = self.v_key.get().strip()
        if not key:
            self._check_show([("Not in your contacts. (Add an API key in Settings to also check links that "
                               "point to a video or an unknown @handle.)", MUTED, [])])
            return
        cost = "100 units" if kind in ("name", "custom") else "1–2 units"
        self._check_show([(f"Not found offline — asking YouTube who that is ({cost})…", MUTED, [])])
        client = YouTubeClient(key, self.store)

        def work():
            try:
                found = client.resolve(kind, val)
                err = None
            except Exception as e:
                found, err = [], str(e)
            self.q.put(("call", lambda: self._check_result(found, err), None))
        threading.Thread(target=work, daemon=True).start()

    def _contacted_line(self, r):
        note = f" · {r['note']}" if r["note"] else ""
        handle = f" (@{r['handle']})" if r["handle"] else ""
        return (f"✓  Already contacted: {r['title']}{handle} — on {_long_date(r['ts'])}{note}", GREEN,
                [("Open channel ↗", lambda u=_channel_url(r): webbrowser.open(u), False)])

    def _check_result(self, found, err):
        if err:
            self._check_show([(f"Couldn't check YouTube: {err}", RED_TEXT, [])])
            return
        if not found:
            self._check_show([("YouTube doesn't know that channel — check the link or the spelling.", MUTED, [])])
            return
        contacted = {r["channel_id"]: r for r in self.store.contacted_rows()}
        lines = []
        for ch in found:
            url = f"https://www.youtube.com/@{ch['handle']}" if ch["handle"] else \
                f"https://www.youtube.com/channel/{ch['channel_id']}"
            if ch["channel_id"] in contacted:
                self.store.update_contacted_info(ch["channel_id"], handle=ch["handle"])
                lines.append(self._contacted_line({**contacted[ch["channel_id"]],
                                                   "handle": contacted[ch["channel_id"]]["handle"] or ch["handle"]}))
                self._render_contacts(highlight=ch["channel_id"])
            else:
                handle = f" (@{ch['handle']})" if ch["handle"] else ""
                lines.append((f"✗  Not contacted yet: {ch['title']}{handle} · {scout.fmt_num(ch['subs'])} subscribers",
                              TEXT, [("Open channel ↗", lambda u=url: webbrowser.open(u), False),
                                     ("Mark as contacted", lambda c=ch, u=url: self._mark_found(c, u), True)]))
        self._check_show(lines)

    def _mark_found(self, ch, url):
        self.store.set_contacted(ch["channel_id"], ch["title"], True, handle=ch["handle"], url=url)
        self._render_contacts(highlight=ch["channel_id"])
        self._render_results()
        self.check_creator()
        self._toast("Marked as contacted")

    def export_contacts(self):
        rows = self.store.contacted_rows()
        if not rows:
            messagebox.showinfo(APP, "Nobody is marked as contacted yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"contacted_{datetime.now():%Y%m%d}.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["channel", "handle", "url", "email", "contacted_on", "note", "channel_id"])
            for r in rows:
                w.writerow([r["title"], r["handle"], _channel_url(r), r["email"],
                            datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d"), r["note"], r["channel_id"]])
        self._toast(f"Exported {len(rows)} contacts")

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
        f = self._page("settings", "Settings", "A YouTube Data API v3 key is free and takes about 3 minutes to set up.")
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
            min_subs=_int(self.v_min.get(), 500),
            max_subs=_int(self.v_max.get(), 2_000_000),
            max_inactive_days=_int(self.v_inactive.get(), 90),
            pages_per_query=max(1, min(5, _int(self.v_pages.get(), 1))),
            max_deep=max(10, min(1000, _int(self.v_deep.get(), 150))),
            videos_per_channel=max(10, min(500, _int(self.v_videos.get(), 100))),
            language=self.v_lang.get().strip().lower(),
            skip_contacted=self.v_skip.get(),
            enrich=self.v_enrich.get(),
            enrich_top=max(0, min(500, _int(self.v_enrich_n.get(), 50))),
            browser_fallback=self.v_browser.get(),
            semantic=self.v_sem.get(),
            sort_by=self.v_sort.get(),
            top_n=_int(self.v_top.get(), 10),
            template=self.t_tpl.get("1.0", "end").rstrip() + "\n",
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

    def _toast(self, msg: str):
        lbl = tk.Label(self, text=f"  {msg}  ", bg=RED, fg="white", font=(UI_SEMI, 10),
                       padx=self.px(10), pady=self.px(6))
        lbl.place(relx=1.0, rely=1.0, x=-self.px(24), y=-self.px(24), anchor="se")
        self.after(1800, lbl.destroy)

    def _clear_cache(self):
        self.store.cache_clear()
        self._toast("Cache cleared")

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
            comparable_games=_lines(cfg["comparable_games"]),
            keywords=_lines(cfg["keywords"]),
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

    def _render_results(self):
        self.tree.delete(*self.tree.get_children())
        self.by_iid.clear()
        top_n = _int(self.v_top.get(), 10)
        times = self.store.contacted_times()
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
                _short_date(times[c.channel_id]) if c.channel_id in times else "", f"{c.total:.0f}",
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
            t.insert("end", f"✓ Contacted on {_long_date(info['ts'])}" +
                     (f" · {info['note']}" if info["note"] else "") + "\n", "good")

        t.insert("end", "EMAILS  (V = email OK · X = wrong email · O = open where it was found)\n", "h")
        if c.emails:
            for e in c.emails:
                conf = e.get("conf", "")
                t.insert("end", "  " + e["email"] + "   ")
                t.insert("end", CONF_LABEL.get(conf, conf), "good" if conf in ("verified", "high") else
                         "warn" if conf == "low" else "mono")
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

    def _clip(self, text: str, what: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self._toast(f"{what} copied")

    _embedder = None

    def _load_embedder(self, progress):
        if App._embedder is None:
            emb, status = embed.load_embedder(progress)
            if emb is None:
                return None, status
            App._embedder = emb
            self.q.put(("call", self._refresh_sem_status, None))
        return App._embedder, "semantic model on (MiniLM)"

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
                App._embedder = emb
            self.q.put(("call", lambda: (self.b_sem.config(state="normal", text="Download now"),
                                   self._refresh_sem_status(),
                                   self._toast("Semantic model ready" if emb else status[:60])), None))
        threading.Thread(target=work, daemon=True).start()

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
        rows = rows[: _int(self.v_top.get(), 10)] if top_only else rows
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

    def _log(self, msg: str):
        self.log_lines.append(f"{datetime.now():%H:%M:%S}  {msg}")
        del self.log_lines[:-500]

    def _on_close(self):
        try:
            save_settings(self._collect_cfg())
        finally:
            self.cancel.set()
            self.destroy()


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


def _short_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b")


def _long_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b %Y")


def _channel_url(r: dict) -> str:
    if r.get("url"):
        return r["url"]
    if r.get("handle"):
        return f"https://www.youtube.com/@{r['handle']}"
    return f"https://www.youtube.com/channel/{r['channel_id']}"


def _lines(s: str) -> list[str]:
    return [x.strip() for x in s.splitlines() if x.strip()]


def _int(s, default: int) -> int:
    try:
        return int(str(s).replace(".", "").replace(",", "").replace(" ", ""))
    except ValueError:
        return default


if __name__ == "__main__":
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens + own taskbar icon
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CreatorScout.App")
        except Exception:
            pass
    App().mainloop()

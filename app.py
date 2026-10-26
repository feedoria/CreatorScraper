"""CreatorScout — find and rank YouTube creators who fit your game.

Run:  python app.py      (or the built CreatorScout.exe)

The window is assembled from one mixin per page (ui/*.py); this file holds the
shared frame: window setup, theme, sidebar navigation, dialogs and toasts.
"""

import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import scout
from storage import Store, load_settings, save_settings

from ui.theme import (
    APP,
    BG,
    BORDER,
    EXAMPLE,
    FAINT,
    GREEN,
    MONO,
    MUTED,
    RED,
    RED_HOVER,
    RED_PRESS,
    RED_TEXT,
    SIDEBAR,
    SURFACE,
    SURFACE2,
    TEXT,
    UI,
    UI_BLACK,
    UI_SEMI,
    resource,
)
from ui.search_page import SearchPage
from ui.results_page import ResultsPage
from ui.contacts_page import ContactsPage
from ui.settings_page import SettingsPage
from ui.runner import Runner

# Re-exported so scripts and tests can patch them in one place (app.enrich.enrich = …).
import enrich  # noqa: E402,F401
import webbrowser  # noqa: E402,F401
from tkinter import filedialog, messagebox  # noqa: E402,F401
from storage import data_dir  # noqa: E402,F401
from youtube_api import YouTubeClient  # noqa: E402,F401
from ui.theme import EMAIL_FILTERS, SORTS  # noqa: E402,F401


class App(SearchPage, ResultsPage, ContactsPage, SettingsPage, Runner, tk.Tk):
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
        self.after(2500, self._auto_check_videos)

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

    def _scrollable(self, parent, key: str) -> ttk.Frame:
        """A frame that scrolls vertically (mouse wheel too) when its content is taller than the window."""
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        win = canvas.create_window(0, 0, window=body, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)

        def refresh(_=None):
            canvas.itemconfigure(win, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), body.winfo_reqheight()))
            if body.winfo_reqheight() > canvas.winfo_height() + 1:
                if not bar.winfo_ismapped():
                    bar.pack(side="right", fill="y", before=canvas)
            elif bar.winfo_ismapped():
                bar.pack_forget()
                canvas.yview_moveto(0)
        canvas.bind("<Configure>", refresh)
        body.bind("<Configure>", refresh)

        def wheel(e):
            if self.current == key and bar.winfo_ismapped() and e.widget.winfo_class() not in ("TCombobox", "Text"):
                step = -1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1
                canvas.yview_scroll(step * 3, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, wheel, add="+")
        canvas.pack(side="left", fill="both", expand=True)
        return body

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

    def _set_badge(self, key: str, count: int):
        """A small red counter next to a menu item (e.g. follow-ups due on Contacts)."""
        badge = self.nav[key][4]
        badge.configure(text=f" {count} " if count else "")
        if self.current:
            self.show(self.current)

    def _refresh_key_status(self):
        if self.v_key.get().strip():
            self.l_key_status.configure(text="●  API key set", fg=GREEN)
        else:
            self.l_key_status.configure(text="●  API key missing", fg=RED_TEXT)

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

    def _toast(self, msg: str):
        lbl = tk.Label(self, text=f"  {msg}  ", bg=RED, fg="white", font=(UI_SEMI, 10),
                       padx=self.px(10), pady=self.px(6))
        lbl.place(relx=1.0, rely=1.0, x=-self.px(24), y=-self.px(24), anchor="se")
        self.after(1800, lbl.destroy)

    def _clip(self, text: str, what: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self._toast(f"{what} copied")

    def _log(self, msg: str):
        self.log_lines.append(f"{datetime.now():%H:%M:%S}  {msg}")
        del self.log_lines[:-500]

    def _on_close(self):
        try:
            save_settings(self._collect_cfg())
        finally:
            self.cancel.set()
            self.destroy()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens + own taskbar icon
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CreatorScout.App")
        except Exception:
            pass
    App().mainloop()

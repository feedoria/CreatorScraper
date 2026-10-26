"""The Contacts page: “have I contacted them?” check and the contacted list.

A mixin for the main App window (see app.py); `self` is the App."""

import csv
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace

import lookup
import outreach
import scout
import steam
from contacts import EMAIL_RE
from storage import save_settings
from youtube_api import YouTubeClient

from ui.theme import APP, CONTACT_FILTERS, FAINT, GREEN, MUTED, RED_TEXT, RED_TINT, SURFACE, TEXT, UI, UI_SEMI
from ui.util import channel_url, log_error, long_date, to_int


class ContactsPage:
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
        tools.pack(fill="x", pady=(p(16), p(6)))
        ttk.Label(tools, text="⌕  Search", style="Muted.TLabel").pack(side="left")
        self.v_cq = tk.StringVar()
        ttk.Entry(tools, textvariable=self.v_cq, width=28).pack(side="left", padx=p(8))
        self.v_cq.trace_add("write", lambda *a: self._render_contacts())
        ttk.Label(tools, text="Show", style="Muted.TLabel").pack(side="left", padx=(p(10), p(8)))
        self.v_cfilter = tk.StringVar(value=CONTACT_FILTERS[0])
        cf = ttk.Combobox(tools, textvariable=self.v_cfilter, values=CONTACT_FILTERS, state="readonly", width=18)
        cf.pack(side="left")
        cf.bind("<<ComboboxSelected>>", lambda e: (cf.selection_clear(), self._render_contacts()))
        self.l_ccount = ttk.Label(tools, text="", style="Muted.TLabel")
        self.l_ccount.pack(side="left", padx=p(12))
        ttk.Button(tools, text="Export CSV", style="Ghost.TButton", command=self.export_contacts).pack(side="right")
        self.b_videos = ttk.Button(tools, text="▶  Check for videos", style="Ghost.TButton",
                                   command=self.check_videos)
        self.b_videos.pack(side="right", padx=p(8))

        acts = ttk.Frame(f)
        acts.pack(fill="x", pady=(0, p(10)))
        ttk.Button(acts, text="Open channel ↗", style="Accent.TButton",
                   command=self._open_contact).pack(side="left")
        ttk.Button(acts, text="✉  Write email", style="Ghost.TButton",
                   command=self.write_contact_email).pack(side="left", padx=(p(8), 0))
        ttk.Label(acts, text="Status", style="Muted.TLabel").pack(side="left", padx=(p(16), p(8)))
        self.v_cstatus = tk.StringVar(value="")
        sb = ttk.Combobox(acts, textvariable=self.v_cstatus, values=outreach.STATUSES, state="readonly", width=16)
        sb.pack(side="left")
        sb.bind("<<ComboboxSelected>>", lambda e: (sb.selection_clear(), self._set_contact_status(self.v_cstatus.get())))
        for label, cmd in (("Open video ↗", self._open_video), ("Edit note", self._edit_note),
                           ("Remove", self._remove_contact)):
            ttk.Button(acts, text=label, style="Ghost.TButton", command=cmd).pack(side="left", padx=(p(8), 0))
        self.l_vstatus = ttk.Label(acts, text="", style="Muted.TLabel")
        self.l_vstatus.pack(side="right")

        o, tf = self._card(f, pad=0)
        o.pack(fill="both", expand=True)
        cols = ("channel", "status", "since", "email", "date", "video", "note")
        heads = ("CHANNEL", "STATUS", "LAST UPDATE", "EMAIL", "CONTACTED ON", "THEIR VIDEO", "NOTE")
        widths = (200, 180, 100, 200, 110, 150, 260)
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
        self.ctree.tag_configure("due", foreground=RED_TEXT)
        self.ctree.tag_configure("published", foreground=GREEN)
        self.ctree.tag_configure("closed", foreground=FAINT)
        self.ctree.bind("<Double-1>", lambda e: self._open_contact())
        self.ctree.bind("<<TreeviewSelect>>", lambda e: self._contact_selected())
        self._render_contacts()

    def _followup_days(self) -> int:
        return to_int(self.v_fu_days.get(), 7) if hasattr(self, "v_fu_days") else int(self.cfg.get("followup_days", 7))

    def _render_contacts(self, highlight: str | None = None):
        if not hasattr(self, "ctree"):
            return
        keep = highlight or (self.ctree.selection() or [None])[0]
        self.ctree.delete(*self.ctree.get_children())
        rows = self.store.contacted_rows()
        days = self._followup_days()
        q = self.v_cq.get().strip().lower()
        flt = self.v_cfilter.get()
        shown = [r for r in rows if not q or q in " ".join(
            [r["title"], r["handle"], r["email"], r["note"], r["channel_id"], r["status"], r["video_title"]]).lower()]
        if flt == "Needs follow-up":
            shown = [r for r in shown if outreach.needs_followup(r, days)]
        elif flt in outreach.STATUSES:
            shown = [r for r in shown if r["status"] == flt]
        for i, r in enumerate(shown):
            due = outreach.needs_followup(r, days)
            tags = ["hit" if r["channel_id"] == highlight else ("odd" if i % 2 else "even")]
            if due:
                tags.append("due")
            elif r["status"] == "Video published":
                tags.append("published")
            elif r["status"] in ("Declined", "No reply"):
                tags.append("closed")
            status = f"{outreach.STATUS_ICON.get(r['status'], '')} {r['status']}" + ("  · follow up!" if due else "")
            ago = outreach.days_since(r["status_ts"])
            video = ""
            if r["video_url"]:
                video = "▶ " + (f"{scout.fmt_num(r['video_views'])} views" if r["video_views"] is not None
                                else "published")
            self.ctree.insert("", "end", iid=r["channel_id"], tags=tags, values=(
                "  " + r["title"] + (f"  @{r['handle']}" if r["handle"] else ""), status,
                "today" if ago == 0 else f"{ago} day{'s' if ago != 1 else ''} ago", r["email"],
                long_date(r["ts"]), video, r["note"]))
        due_n = outreach.followup_count(rows, days)
        text = f"{len(shown)} of {len(rows)} contacts" if (q or flt != CONTACT_FILTERS[0]) else f"{len(rows)} contacts"
        if due_n:
            text += f" · {due_n} need a follow-up"
        self.l_ccount.config(text=text)
        self._set_badge("contacts", due_n)
        if keep and self.ctree.exists(keep):
            self.ctree.selection_set(keep)
            self.ctree.see(keep)

    def _contact_selected(self):
        sel = self.ctree.selection()
        r = next((r for r in self.store.contacted_rows() if sel and r["channel_id"] == sel[0]), None)
        self.v_cstatus.set(r["status"] if r else "")

    def _set_contact_status(self, status: str):
        r = self._contact_row()
        if not r or not status:
            return
        self.store.set_status(r["channel_id"], status)
        self._render_contacts(highlight=r["channel_id"])
        self._render_results()
        self._toast(f"{r['title']}: {status}")

    def _open_video(self):
        r = self._contact_row()
        if not r:
            return
        if r["video_url"]:
            webbrowser.open(r["video_url"])
        else:
            messagebox.showinfo(APP, f"No video about your game found for {r['title']} yet.\n\n"
                                     "Click “Check for videos” to look at their latest uploads.")

    # ---- writing emails (opens a pre-filled draft; nothing is sent automatically)
    def _open_mail(self, to: str, draft: str) -> None:
        subject, body = outreach.split_draft(draft)
        app = self.v_mail.get() if hasattr(self, "v_mail") else self.cfg.get("mail_app", outreach.MAIL_APPS[0])
        webbrowser.open(outreach.compose_url(app, to, subject, body))

    def _ask_email_then(self, title: str, current: str, then):
        if current:
            then(current)
            return
        def got(val):
            if EMAIL_RE.fullmatch(val or ""):
                then(val)
            elif val:
                messagebox.showwarning(APP, f"“{val}” doesn't look like an email address.")
        self._ask_text(f"Email · {title}", "No email saved for this creator — paste it here "
                                           "(e.g. from their YouTube About tab).", "", got)

    def write_contact_email(self):
        r = self._contact_row()
        if not r:
            return
        cfg = self._collect_cfg()
        followup = r["status"] in outreach.WAITING
        if followup:
            draft = outreach.followup_draft(r["title"], cfg.get("game_name", ""))
        else:
            c = next((c for c in self.results if c.channel_id == r["channel_id"]), None) or \
                SimpleNamespace(title=r["title"], matched_games=set())
            draft = scout.draft_email(c, cfg, cfg["template"])

        def go(email):
            if email != r["email"]:
                self.store.set_contacted(r["channel_id"], r["title"], True, email=email)
            self._open_mail(email, draft)
            if followup and messagebox.askyesno(APP, f"Mark {r['title']} as “Followed up”?\n\n"
                                                     "(The follow-up reminder restarts from today.)"):
                self.store.set_status(r["channel_id"], "Followed up")
            self._render_contacts(highlight=r["channel_id"])
        self._ask_email_then(r["title"], r["email"], go)

    # ---- spotting the video they published about your game -------------------
    def _auto_check_videos(self):
        """Once a day, quietly look for videos about your game from creators you contacted."""
        if not self.cfg.get("auto_video_check", True) or not self.v_key.get().strip():
            return
        if time.time() - float(self.cfg.get("last_video_check", 0) or 0) < 86400:
            return
        if any(r["status"] not in ("Declined", "Video published") for r in self.store.contacted_rows()):
            self.check_videos(quiet=True)

    def check_videos(self, quiet: bool = False):
        key = self.v_key.get().strip()
        cfg = self._collect_cfg()
        game = cfg.get("game_name", "").strip()
        rows = self.store.contacted_rows()
        if not quiet:
            if not key:
                messagebox.showwarning(APP, "Add your YouTube API key in Settings first.")
                return
            if not game:
                messagebox.showwarning(APP, "Fill in your game's name on the “Your game” page first.")
                return
            if not rows:
                messagebox.showinfo(APP, "Nobody is marked as contacted yet.")
                return
        if not key or not game or getattr(self, "_checking_videos", False):
            return
        self._checking_videos = True
        self.b_videos.state(["disabled"])
        todo = sum(r["status"] not in ("Declined", "Video published") for r in rows)
        self.l_vstatus.config(text=f"Looking for videos about {game} (≈{2 * todo + 1} units)…")
        client = YouTubeClient(key, self.store)
        steam_ref = cfg.get("steam_ref", "").strip()
        store = self.store

        def work():
            found, err, appid = [], None, None
            try:
                if steam_ref:
                    try:
                        hit = steam.Steam(store).find_app(steam_ref)
                        appid = hit[0] if hit else None
                    except Exception:  # noqa: BLE001 — the Steam link only adds precision
                        appid = None
                found = outreach.find_published(
                    client, rows, game, appid,
                    progress=lambda m: self.q.put(("call", lambda m=m: self.l_vstatus.config(text=m), None)),
                    cancel=self.cancel)
            except Exception as e:  # noqa: BLE001
                err = str(e)
                log_error("check_videos")
            self.q.put(("call", lambda: self._videos_done(found, err, quiet, client.units_used), None))
        threading.Thread(target=work, daemon=True).start()

    def _videos_done(self, found, err, quiet, units):
        self._checking_videos = False
        self.b_videos.state(["!disabled"])
        if err:
            self.l_vstatus.config(text="")
            if not quiet:
                messagebox.showerror(APP, f"Couldn't check for videos:\n{err}")
            return
        for r, v in found:
            self.store.set_video(r["channel_id"], v["url"], v["title"], v["views"])
        self.cfg["last_video_check"] = time.time()
        save_settings(self._collect_cfg())
        self.l_vstatus.config(text=f"Checked {datetime.now():%H:%M} · {units} units")
        self._render_contacts()
        self._render_results()
        if found:
            names = ", ".join(r["title"] for r, _ in found[:5]) + (" …" if len(found) > 5 else "")
            self._toast(f"▶ {len(found)} new video{'s' if len(found) != 1 else ''} about your game")
            if not quiet:
                messagebox.showinfo(APP, f"New videos about your game from: {names}.\n\n"
                                         "Their status is now “Video published”.")
        elif not quiet:
            self._toast("No new videos about your game yet")

    def _contact_row(self):
        sel = self.ctree.selection()
        if not sel:
            messagebox.showinfo(APP, "Select someone in the list first.")
            return None
        return next((r for r in self.store.contacted_rows() if r["channel_id"] == sel[0]), None)

    def _open_contact(self):
        r = self._contact_row()
        if r:
            webbrowser.open(channel_url(r))

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
        status = f" · {r['status']}" if r.get("status") and r["status"] != "Contacted" else ""
        return (f"✓  Already contacted: {r['title']}{handle} — on {long_date(r['ts'])}{status}{note}", GREEN,
                [("Open channel ↗", lambda u=channel_url(r): webbrowser.open(u), False)])

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
            w.writerow(["channel", "handle", "url", "email", "contacted_on", "status", "status_since",
                        "video_url", "video_title", "video_views", "note", "channel_id"])
            for r in rows:
                w.writerow([r["title"], r["handle"], channel_url(r), r["email"],
                            datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d"), r["status"],
                            datetime.fromtimestamp(r["status_ts"]).strftime("%Y-%m-%d"), r["video_url"],
                            r["video_title"], "" if r["video_views"] is None else r["video_views"],
                            r["note"], r["channel_id"]])
        self._toast(f"Exported {len(rows)} contacts")

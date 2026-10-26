"""End-to-end GUI test: search → Write email → outreach statuses → follow-up reminder
→ spotting the published video. Runs the real window against the simulated YouTube.

Needs a display (on Linux CI: xvfb-run). Skips cleanly when there is none.
"""
import os, sys, tempfile, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["APPDATA"] = tempfile.mkdtemp()          # never touch the real settings
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))

import tkinter as tk
try:
    tk.Tk().destroy()
except tk.TclError as e:
    print(f"GUI TEST SKIPPED (no display: {e})")
    sys.exit(0)

import app, youtube_api
from fake_youtube import World, GAMES

DAY = 86400
w = World(seed=7, n=90)
orig_init = youtube_api.YouTubeClient.__init__
app.YouTubeClient.__init__ = lambda self, k, st, session=None: orig_init(self, k, st, w.session())
app.enrich.enrich = lambda *a, **k: {"checked": 0, "found_http": 0, "found_browser": 0, "pages": 0, "browser": "off"}
app.App._steam_appids = lambda self, games: {}       # no network in tests
opened, asked = [], []
app.webbrowser.open = lambda url: opened.append(url)
app.messagebox.askyesno = lambda title, msg: asked.append(msg) or True
app.messagebox.showinfo = lambda *a, **k: None
app.messagebox.showwarning = lambda *a, **k: print("WARNING", a)
app.messagebox.showerror = lambda *a, **k: print("ERROR", a)


def wait(cond, timeout=60.0, what=""):
    end = time.time() + timeout
    while time.time() < end:
        a.update()
        if cond():
            return
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for {what}")


a = app.App()
a.update()
# the window is assembled from one mixin per page
names = [c.__name__ for c in type(a).__mro__]
assert all(n in names for n in ("SearchPage", "ResultsPage", "ContactsPage", "SettingsPage", "Runner")), names

# ---- 1. a search ---------------------------------------------------------------
a.v_key.set("AIzaFAKE"); a._save_key()
a.e_name.delete(0, "end"); a.e_name.insert(0, "Digger's End")
a.v_max.set("5000000")
a.t_games.delete("1.0", "end"); a.t_games.insert("1.0", "\n".join(GAMES))
a.start()
wait(lambda: a.results and str(a.b_run.cget("state")) == "normal", what="search results")
rows = a.tree.get_children()
assert len(rows) > 10, len(rows)
print(f"search: {len(rows)} creators ranked ✓")

# ---- 2. Write email from Results (draft only, then mark as contacted) ----------
a.tree.selection_set(rows[0]); a.update()
c = a._selected()
a.store.set_manual_email(c.channel_id, "mole@digs.tv"); a._apply_manual([c])
a.write_email(); a.update()
url = urlparse(opened[-1]); q = parse_qs(url.query)
assert url.netloc == "mail.google.com" and q["to"] == ["mole@digs.tv"], opened[-1]
assert q["su"][0].startswith("Digger's End") and q["body"][0].startswith(f"Hi {c.title}"), q
assert "Subject:" not in q["body"][0]
assert c.channel_id in a.store.contacted_ids() and any("Mark" in m for m in asked)
print("Write email: Gmail draft with subject + body, then marked as contacted ✓")

# a second creator, contacted 12 days ago and never answered
a.tree.selection_set(a.tree.get_children()[1]); a.update()
c2 = a._selected()
a.toggle_contacted()
with a.store._lock:
    a.store._db.execute("UPDATE contacted SET ts=?, status_ts=? WHERE channel_id=?",
                        (time.time() - 12 * DAY, time.time() - 12 * DAY, c2.channel_id))
    a.store._db.commit()

# ---- 3. Contacts page: statuses, reminders, badge, filter ---------------------
a.show("contacts"); a._render_contacts(); a.update()
assert set(a.ctree.get_children()) == {c.channel_id, c2.channel_id}
assert "follow up!" in a.ctree.set(c2.channel_id, "status") and "due" in a.ctree.item(c2.channel_id, "tags")
assert a.nav["contacts"][4].cget("text").strip() == "1", a.nav["contacts"][4].cget("text")
assert "1 need a follow-up" in a.l_ccount.cget("text")
a.v_cfilter.set("Needs follow-up"); a._render_contacts()
assert a.ctree.get_children() == (c2.channel_id,)
a.v_cfilter.set("Everyone"); a._render_contacts()
a.ctree.selection_set(c.channel_id); a.update()
assert a.v_cstatus.get() == "Contacted"
a.v_cstatus.set("Key sent"); a._set_contact_status("Key sent"); a.update()
assert "Key sent" in a.ctree.set(c.channel_id, "status")
print("contacts: status changes, follow-up reminder, sidebar badge and filter ✓")

# follow-up email from Contacts → asks to mark "Followed up" → reminder clears
a.store.set_contacted(c2.channel_id, c2.title, True, email="c2@creator.tv")
a.ctree.selection_set(c2.channel_id); a.update()
a.v_mail.set("Default mail app")
a.write_contact_email(); a.update()
assert opened[-1].startswith("mailto:c2@creator.tv?subject=Re%3A%20Digger"), opened[-1]
r2 = next(r for r in a.store.contacted_rows() if r["channel_id"] == c2.channel_id)
assert r2["status"] == "Followed up" and a.nav["contacts"][4].cget("text") == ""
print("follow-up: mailto draft 'Re: …', status → Followed up, badge cleared ✓")

# ---- 4. the creator published a video about the game ------------------------
v = w.ch[c2.channel_id]["vids"][0]
v.update(title="Digger's End is the best digging game", age=1, views=12345)
a.store.cache_clear()                                  # the real API would show the new upload
a.check_videos()
wait(lambda: not getattr(a, "_checking_videos", False), what="video check")
r2 = next(r for r in a.store.contacted_rows() if r["channel_id"] == c2.channel_id)
assert r2["status"] == "Video published" and r2["video_views"] == 12345, r2
assert "12.3k views" in a.ctree.set(c2.channel_id, "video") or "12k" in a.ctree.set(c2.channel_id, "video"), \
    a.ctree.set(c2.channel_id, "video")
a.ctree.selection_set(c2.channel_id); a._open_video()
assert opened[-1] == f"https://www.youtube.com/watch?v={c2.channel_id}_0"
a.show("results"); a._render_results()
cells = [a.tree.set(i, "done") for i in a.tree.get_children() if a.by_iid[i] is c2]
assert cells and "Video published" in cells[0], cells
print("published video: detected, status updated, views shown, link opens ✓")

# ---- 5. settings survive a restart -------------------------------------------
a.v_fu_days.set("10")
a._on_close()
cfg = app.load_settings() if hasattr(app, "load_settings") else __import__("storage").load_settings()
assert cfg["followup_days"] == 10 and cfg["mail_app"] == "Default mail app" and cfg["last_video_check"] > 0
print("GUI TESTS PASSED")

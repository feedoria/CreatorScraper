"""Real-world cases that were wrong in the first live run."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contacts import own_links, rate_email
from scout import Candidate, youtube_emails

# 1) addresses that are not the creator's must be dropped
assert rate_email("support@enshrouded.zendesk.com", "Insym", "insym", "https://enshrouded.com")[0] is None
assert rate_email("merch@gamegrumps.com", "GameGrumps", "gamegrumps")[0] is None
assert rate_email("noreply@insym.tv", "Insym", "insym")[0] is None
assert rate_email("press@devolverdigital.com", "Insym", "insym")[0] == "medium"   # agency-ish, flagged for review
# 2) believable ones are kept and ranked
assert rate_email("commissarkaibuisness@gmail.com", "CommissarKai", "commissarkai")[0] == "high"
assert rate_email("hello@coolsite.dev", "Bob", "bob", "https://coolsite.dev/contact")[0] == "high"
assert rate_email("someone@random-company.com", "Insym", "insym")[0] == "low"
print("rate_email: helpdesk/merch dropped, real business emails kept ✓")

# 3) one-off links in a single video (sponsor / game page) are not 'their' links
desc = "Business: see links below https://linktr.ee/insym"
vids = ["Play Enshrouded: https://enshrouded.com  Sponsored by X"] + ["Twitter https://twitter.com/insym  Merch https://insym.store"] * 4
links = own_links(desc, vids)
urls = [l["url"] for l in links]
assert "https://enshrouded.com" not in urls and "https://linktr.ee/insym" in urls and "https://insym.store" in urls, urls
print("own_links: one-off sponsor/game links ignored, repeated footer links kept ✓", urls)

# 4) emails in video descriptions: a one-off unrelated address is ignored
c = Candidate(channel_id="UCx", title="Insym", custom_url="@insym", description="Gaming, mostly horror and survival.")
c.recent = [{"description": "Thanks to the devs! Contact them at partners@somestudio.com", "title": "t", "published": None}] + \
           [{"description": "Business inquiries: insymbiz@gmail.com", "title": "t", "published": None}] * 3
em = youtube_emails(c)
assert [e["email"] for e in em] == ["insymbiz@gmail.com"] and em[0]["conf"] == "high", em
print("youtube_emails: sponsor address in one video ignored, creator's business email kept ✓")
print("EMAIL PRECISION TESTS PASSED")

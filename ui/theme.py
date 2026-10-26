"""Look and feel shared by every page: fonts, colours, constants, bundled resources."""

import os
import sys

APP = "CreatorScout"
SORTS = ["Expected views per pitch", "Coverage chance", "Match score", "Name (A–Z)",
         "Subscribers (largest first)", "Subscribers (smallest first)"]
HEADS = {"rank": "#", "channel": "CHANNEL", "chance": "CHANCE", "exp": "EXP. VIEWS", "pred": "PRED. VIEWS",
         "subs": "SUBS", "keys": "KEYS", "games": "SIMILAR GAMES", "last": "LAST UPLOAD", "email": "EMAIL",
         "source": "FOUND ON", "check": "EMAIL CHECK", "done": "CONTACTED", "match": "MATCH"}
TEXT_COLS = ("channel", "email", "source", "check")
EMAIL_FILTERS = ["All creators", "With email", "Emails to check", "Verified emails", "No email yet"]
CONTACT_FILTERS = ["Everyone", "Needs follow-up", "Contacted", "Followed up", "Replied", "Key sent",
                   "Video published", "Declined", "No reply"]
CONF_LABEL = {"verified": "✓ verified", "high": "High", "medium": "Medium", "low": "Low · check",
              "shared": "Shared · agency?"}
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
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, name)

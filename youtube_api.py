"""Thin YouTube Data API v3 client with caching and quota accounting.

Uses the official API only (no HTML scraping, no Selenium).
Quota costs: search.list = 100 units, channels/playlistItems/videos.list = 1 unit.
Default daily quota is 10,000 units.
"""
import json
import time

import requests

API = "https://www.googleapis.com/youtube/v3/"
COST = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}
DAY = 86400.0
TTL = {"search": 3 * DAY, "channels": 7 * DAY, "playlistItems": 2 * DAY, "videos": 2 * DAY}


class QuotaExceeded(Exception):
    pass


class ApiError(Exception):
    pass


class YouTubeClient:
    def __init__(self, api_key: str, store, session=None):
        self.api_key = api_key.strip()
        self.store = store
        self.session = session or requests.Session()
        self.units_used = 0
        self.cache_hits = 0

    def _get(self, endpoint: str, params: dict) -> dict:
        key = endpoint + "?" + json.dumps(sorted(params.items()))
        cached = self.store.cache_get(key, TTL[endpoint])
        if cached is not None:
            self.cache_hits += 1
            return cached

        last_err = None
        for attempt in range(4):
            try:
                r = self.session.get(
                    API + endpoint, params={**params, "key": self.api_key}, timeout=25
                )
            except (requests.RequestException, UnicodeError) as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
                continue

            if r.status_code == 200:
                self.units_used += COST[endpoint]
                try:
                    body = r.json()
                except ValueError as e:  # truncated / badly encoded body -> try again
                    last_err = e
                    time.sleep(1.5 * (attempt + 1))
                    continue
                self.store.cache_put(key, body)
                return body

            reason, message = _error_reason(r)
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise QuotaExceeded(message or reason)
            if reason in ("keyInvalid", "badRequest") and "key" in (message or "").lower():
                raise ApiError("The API key is invalid. Check the Settings page.")
            if reason == "accessNotConfigured" or "has not been used" in (message or ""):
                raise ApiError(
                    "YouTube Data API v3 is not enabled for your key's project "
                    "(Google Cloud Console → APIs & Services → Enable APIs)."
                )
            if r.status_code == 404:
                return {"items": []}
            if r.status_code >= 500:
                last_err = ApiError(f"{r.status_code} {message}")
                time.sleep(1.5 * (attempt + 1))
                continue
            raise ApiError(f"YouTube API error {r.status_code}: {message or reason}")
        raise ApiError(f"Could not reach the YouTube API: {last_err}")

    # ---- endpoints -------------------------------------------------------
    def search_videos(self, query: str, pages: int = 1, language: str = "") -> list[dict]:
        out, token = [], None
        for _ in range(max(1, pages)):
            params = {
                "part": "snippet",
                "type": "video",
                "q": query,
                "maxResults": 50,
                "order": "relevance",
            }
            if language:
                params["relevanceLanguage"] = language
            if token:
                params["pageToken"] = token
            body = self._get("search", params)
            for it in body.get("items", []):
                sn = it.get("snippet", {})
                out.append(
                    {
                        "video_id": it.get("id", {}).get("videoId"),
                        "channel_id": sn.get("channelId"),
                        "channel_title": sn.get("channelTitle", ""),
                        "title": sn.get("title", ""),
                    }
                )
            token = body.get("nextPageToken")
            if not token:
                break
        return out

    def channels(self, ids: list[str]) -> list[dict]:
        out = []
        ids = [i for i in dict.fromkeys(ids) if i]
        for i in range(0, len(ids), 50):
            body = self._get(
                "channels",
                {
                    "part": "snippet,statistics,contentDetails,brandingSettings",
                    "id": ",".join(ids[i : i + 50]),
                    "maxResults": 50,
                },
            )
            out.extend(body.get("items", []))
        return out

    def uploads(self, playlist_id: str, n: int = 30) -> list[dict]:
        if not playlist_id:
            return []
        body = self._get(
            "playlistItems",
            {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": min(50, n)},
        )
        out = []
        for it in body.get("items", []):
            sn, cd = it.get("snippet", {}), it.get("contentDetails", {})
            out.append(
                {
                    "video_id": cd.get("videoId"),
                    "title": sn.get("title", ""),
                    "description": sn.get("description", ""),
                    "published": cd.get("videoPublishedAt") or sn.get("publishedAt"),
                }
            )
        return out

    # ---- resolving one channel (for "have I contacted them?") -------------
    @staticmethod
    def _brief(item: dict) -> dict:
        sn, st = item.get("snippet", {}), item.get("statistics", {})
        return {"channel_id": item.get("id", ""), "title": sn.get("title", ""),
                "handle": (sn.get("customUrl") or "").lstrip("@").lower(),
                "subs": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0) or 0)}

    def resolve(self, kind: str, value: str) -> list[dict]:
        """Channel(s) for a parsed reference. 1 unit, except a name search (100)."""
        base = {"part": "snippet,statistics", "maxResults": 5}
        if kind == "id":
            items = self._get("channels", {**base, "id": value}).get("items", [])
        elif kind == "handle":
            items = self._get("channels", {**base, "forHandle": "@" + value}).get("items", [])
        elif kind == "user":
            items = self._get("channels", {**base, "forUsername": value}).get("items", [])
        elif kind == "video":
            vids = self._get("videos", {"part": "snippet", "id": value}).get("items", [])
            if not vids:
                return []
            cid = vids[0].get("snippet", {}).get("channelId", "")
            items = self._get("channels", {**base, "id": cid}).get("items", []) if cid else []
        elif kind in ("name", "custom"):
            hits = self._get("search", {"part": "snippet", "type": "channel", "q": value,
                                        "maxResults": 5}).get("items", [])
            ids = [h.get("id", {}).get("channelId") for h in hits if h.get("id", {}).get("channelId")]
            items = self._get("channels", {**base, "id": ",".join(ids)}).get("items", []) if ids else []
        else:
            return []
        return [self._brief(i) for i in items]

    def video_views(self, ids: list[str]) -> dict[str, int]:
        views = {}
        ids = [i for i in ids if i]
        for i in range(0, len(ids), 50):
            body = self._get(
                "videos", {"part": "statistics", "id": ",".join(ids[i : i + 50]), "maxResults": 50}
            )
            for it in body.get("items", []):
                v = it.get("statistics", {}).get("viewCount")
                if v is not None:
                    views[it["id"]] = int(v)
        return views


def _error_reason(r) -> tuple[str, str]:
    try:
        err = r.json().get("error", {})
        reasons = err.get("errors") or [{}]
        return reasons[0].get("reason", ""), err.get("message", "")
    except ValueError:
        return "", r.text[:200]

"""
Thin client around the MediaWiki Action API, tuned for Fandom wikis.

Fandom wikis all expose the standard MediaWiki API at <base_url>/api.php,
so everything here is generic MediaWiki, not Fandom-specific.
"""
from __future__ import annotations
import time
import requests
from urllib.parse import urljoin

USER_AGENT = "hieroglyphs/1.0 (fandom dictionary builder; contact: local-user)"

# MediaWiki API endpoints commonly live at one of these paths - Fandom
# always uses the first, but we try the others too for general MediaWiki
# sites (mirrors Runik's own endpoint-detection behaviour).
API_SUFFIXES = ["/api.php", "/w/api.php", "/wiki/api.php"]

# Large wikis (Fandom especially) will occasionally answer a burst of
# requests with a transient 503/429 rather than a real failure. Retrying
# with backoff clears the overwhelming majority of these automatically,
# so a crawl doesn't need a human to intervene for something that
# resolves itself in a few seconds.
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
MAX_RETRIES = 5
BACKOFF_SECONDS = [2, 5, 10, 20, 40]


class TransientAPIError(Exception):
    """Raised when the wiki API failed even after all automatic retries -
    the crawl can still be resumed from where it stopped."""


class WikiClient:
    def __init__(self, base_url: str, session: requests.Session | None = None):
        base_url = base_url.strip().rstrip("/")
        if "/wiki/" in base_url:
            base_url = base_url.split("/wiki/")[0]
        self.base_url = base_url
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.api_url = self._resolve_api_url(base_url)
        # Updated right before every generator=allpages request, so a
        # caller can always ask "where were we?" to resume a crawl after
        # a pause, a stop, or an unrecoverable error.
        self.current_gapfrom: str = ""

    def _resolve_api_url(self, base_url: str) -> str:
        for suffix in API_SUFFIXES:
            candidate = base_url + suffix
            try:
                r = self.session.get(candidate, params={
                    "action": "query", "meta": "siteinfo", "siprop": "general", "format": "json",
                }, timeout=8)
                if r.ok and "query" in r.json():
                    return candidate
            except Exception:
                continue
        # fall back to the Fandom/MediaWiki default even if detection failed,
        # so later calls at least produce a clear connection error
        return base_url + "/api.php"

    def _get(self, params: dict, on_retry=None) -> dict:
        params = {**params, "format": "json"}
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = self.session.get(self.api_url, params=params, timeout=30)
                if r.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                    wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                    if on_retry:
                        on_retry(attempt + 1, MAX_RETRIES, wait, r.status_code)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                    if on_retry:
                        on_retry(attempt + 1, MAX_RETRIES, wait, "connection error")
                    time.sleep(wait)
                    continue
                raise TransientAPIError(str(exc)) from exc
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                    wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                    if on_retry:
                        on_retry(attempt + 1, MAX_RETRIES, wait, status)
                    time.sleep(wait)
                    continue
                if status in RETRYABLE_STATUS_CODES:
                    raise TransientAPIError(str(exc)) from exc
                raise
        raise TransientAPIError(str(last_exc) if last_exc else "request failed after retries")

    def count_pages(self) -> int:
        """Fast page count via siteinfo statistics (feature 11)."""
        data = self._get({"action": "query", "meta": "siteinfo", "siprop": "statistics"})
        return int(data["query"]["statistics"]["articles"])

    def iter_content_pages(self, batch_size: int = 30, start_gapfrom: str = "", on_retry=None, request_delay: float = 0.2):
        """
        Crawl every non-redirect article, yielding (title, wikitext,
        redirect_titles) tuples.

        This mirrors Runik's own approach exactly: a single
        generator=allpages query with prop=redirects|revisions pulls a
        page's content AND the list of pages that redirect to it in one
        correlated response - MediaWiki itself does the title matching,
        so there's no separate "does this redirect target match that
        headword" step to get wrong (which was the actual bug in the
        earlier two-pass implementation).

        If a page has more inbound redirects than fit in one response
        (rdlimit), the API returns `rdcontinue` and re-sends the *same*
        page batch with additional redirects on the next call - in that
        case wikitext is yielded as None to signal "just merge these
        extra aliases into the entry you already built for this title".

        start_gapfrom lets a caller resume a previously interrupted crawl
        from the exact page it stopped at, instead of starting over.
        request_delay adds a small, polite pause between requests to
        avoid tripping a wiki's rate limiting in the first place.
        """
        gapfrom = start_gapfrom
        rdcontinue = ""
        while True:
            self.current_gapfrom = gapfrom
            params = {
                "action": "query",
                "generator": "allpages",
                "gaplimit": str(batch_size),
                "gapnamespace": "0",
                "gapfilterredir": "nonredirects",
                "prop": "redirects|revisions",
                "rvprop": "content",
                "rvslots": "main",
                "rdprop": "title",
                "rdlimit": "max",
            }
            if gapfrom:
                params["gapfrom"] = gapfrom
            continuing_redirects = bool(rdcontinue)
            if rdcontinue:
                params["rdcontinue"] = rdcontinue

            data = self._get(params, on_retry=on_retry)
            pages = data.get("query", {}).get("pages", {})
            for _, p in pages.items():
                title = p.get("title")
                if not title:
                    continue
                redirect_titles = [r["title"] for r in p.get("redirects", []) if r.get("title")]
                if continuing_redirects:
                    yield title, None, redirect_titles
                    continue
                wikitext = None
                revs = p.get("revisions") or []
                if revs:
                    slot = revs[0].get("slots", {}).get("main", {})
                    wikitext = slot.get("*", slot.get("content"))
                yield title, wikitext, redirect_titles

            cont = data.get("continue", {})
            new_rdcontinue = cont.get("rdcontinue")
            new_gapcontinue = cont.get("gapcontinue")
            if new_rdcontinue:
                rdcontinue = new_rdcontinue
            elif new_gapcontinue:
                gapfrom = new_gapcontinue
                rdcontinue = ""
            else:
                break
            if request_delay:
                time.sleep(request_delay)

    def get_wikitext(self, title: str) -> str | None:
        """Fetch the raw wikitext source of a single page (used for
        one-off lookups outside the main crawl loop)."""
        try:
            data = self._get({
                "action": "parse", "page": title, "prop": "wikitext", "redirects": "1",
            })
            return data["parse"]["wikitext"]["*"]
        except Exception:
            return None

    def article_url(self, title: str) -> str:
        return urljoin(self.base_url + "/wiki/", title.replace(" ", "_"))

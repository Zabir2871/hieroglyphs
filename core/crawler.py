from __future__ import annotations
import threading
import time
from core.wiki_client import WikiClient, TransientAPIError
from core.wikitext_parser import build_section_tree
from core.entry_builder import build_entries_for_page, attach_see_more_sections, PRIORITY_REDIRECT
from core.models import Dictionary, Entry

BATCH_SIZE = 30
REQUEST_DELAY = 0.2  # polite pause between requests, reduces rate-limit hits


def _resolve_alias_candidates(candidates: list[tuple[Entry, str, int]], all_entries: list[Entry]):
    """
    Apply every alias candidate to its entry, with higher-confidence
    sources always winning a collision regardless of which page was
    crawled first - see the PRIORITY_* constants in entry_builder.py for
    why a flat "root beats sub-entry" (or vice versa) rule isn't enough:
    a real page's own redirect/infobox alias should beat a coincidental
    subsection heading elsewhere ("Mustang" -> Virginia au Augustus, not
    a "Relationships: Mustang" aside on someone else's page), while a
    heading's own specific name should beat a vague prose guess made
    about an unrelated page ("Cupid's Kiss" -> the "Technology: Cupid's
    Kiss" heading that actually defines it, not "Technology" itself just
    because its intro happened to mention the name).

    An alias is also never allowed to shadow another entry's real
    headword - a dedicated page always wins outright.
    """
    real_headwords = {e.headword.lower() for e in all_entries}
    owner: dict[str, tuple[Entry, int]] = {}

    # Highest confidence first; within equal confidence, first claim wins.
    for entry, alias, priority in sorted(candidates, key=lambda c: -c[2]):
        alias = alias.strip()
        if not alias:
            continue
        alias_lower = alias.lower()
        if alias_lower == entry.headword.lower():
            continue
        if alias_lower in real_headwords and alias_lower != entry.headword.lower():
            continue  # never let an alias shadow someone else's real page

        existing = owner.get(alias_lower)
        if existing is None:
            owner[alias_lower] = (entry, priority)
            if alias not in entry.aliases:
                entry.aliases.append(alias)
        else:
            existing_entry, existing_priority = existing
            if existing_entry is entry:
                continue
            if priority > existing_priority:
                if alias in existing_entry.aliases:
                    existing_entry.aliases.remove(alias)
                owner[alias_lower] = (entry, priority)
                if alias not in entry.aliases:
                    entry.aliases.append(alias)
            # priority <= existing: the earlier, higher-or-equal claim keeps it


class CrawlJob:
    """
    Holds live progress state for one crawl, polled by the frontend
    (page counter, processed/total counters, progress bar), and supports
    pause/resume/stop plus automatic recovery after a transient API
    failure (e.g. Fandom rate-limiting a large wiki mid-crawl).
    """
    def __init__(self, job_id: str, base_url: str, dict_name: str, apply_tense_fix: bool = True):
        self.job_id = job_id
        self.base_url = base_url
        self.dict_name = dict_name
        self.apply_tense_fix = apply_tense_fix

        # counting -> crawling -> done | error | stopped
        # (crawling can also be observed as "paused" via the paused flag)
        self.status = "counting"
        self.total_pages = 0
        self.processed_pages = 0
        self.redirect_pages = 0
        self.entries_created = 0
        self.current_title = ""
        self.error = None
        self.retry_info = None  # e.g. "retry 2/5 in 5s (503)" while backing off
        self.dictionary: Dictionary | None = None

        self.paused = False
        self.stop_requested = False
        self.can_resume = False
        self.resume_cursor = ""  # gapfrom to continue from

        # work-in-progress state, preserved across a pause/error/resume so
        # nothing already crawled is ever thrown away
        self._wip_dictionary: Dictionary | None = None
        self._wip_candidates: list = []
        self._wip_root_entry_by_title: dict = {}
        self._wip_total_redirects = 0

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def to_dict(self):
        with self._lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "total_pages": self.total_pages,
                "processed_pages": self.processed_pages,
                "redirect_pages": self.redirect_pages,
                "entries_created": self.entries_created,
                "current_title": self.current_title,
                "error": self.error,
                "retry_info": self.retry_info,
                "paused": self.paused,
                "can_resume": self.can_resume,
                "percent": round(100 * self.processed_pages / self.total_pages, 1) if self.total_pages else 0,
            }

    def start(self, resuming: bool = False):
        self._thread = threading.Thread(target=self._run, kwargs={"resuming": resuming}, daemon=True)
        self._thread.start()

    def pause(self):
        """Pause between pages - the crawl thread is still alive and will
        continue exactly where it left off as soon as resume() is called,
        no re-fetching needed. A no-op if the crawl has already finished
        (guards against a pause request racing with completion on a very
        fast/small crawl and permanently mislabeling a done job)."""
        with self._lock:
            if self.status in ("crawling", "counting"):
                self.paused = True
                self.status = "paused"

    def resume(self):
        """Resume a paused crawl (thread still alive, just unblocks it) or
        restart the underlying request loop after a fatal/transient error
        (thread has exited - spawn a fresh one continuing from the exact
        page we stopped at, reusing everything already collected)."""
        with self._lock:
            needs_new_thread = self.status == "error"
            was_paused = self.status == "paused"
            self.paused = False
            self.stop_requested = False
            if needs_new_thread:
                self.status = "crawling"
                self.error = None
                self.retry_info = None
            elif was_paused:
                # The thread is still alive, blocked in _wait_while_paused;
                # reflect that it's actively crawling again immediately,
                # rather than waiting for the next loop iteration to
                # relabel it (and in case the crawl had *just* finished
                # while paused was still technically true, leave status
                # alone if it's no longer actually "paused").
                self.status = "crawling"
        if needs_new_thread:
            self.start(resuming=True)

    def stop(self):
        """Stop the crawl early but keep everything collected so far -
        the partial dictionary is still perfectly valid to commit."""
        self._set(stop_requested=True, paused=False)

    def _set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _wait_while_paused(self) -> bool:
        """Blocks while paused; returns True if a stop was requested
        (either while paused or just now) so the caller should break."""
        while True:
            with self._lock:
                if self.stop_requested:
                    return True
                if not self.paused:
                    return False
            time.sleep(0.4)

    def _on_retry(self, attempt, max_attempts, wait_seconds, reason):
        self._set(retry_info=f"Wiki responded slowly (attempt {attempt}/{max_attempts}, retrying in {wait_seconds}s, reason: {reason})")

    def _run(self, resuming: bool = False):
        client = WikiClient(self.base_url)
        dictionary = None
        all_candidates: list[tuple[Entry, str, int]] = []
        root_entry_by_title: dict[str, Entry] = {}
        total_redirects = 0
        start_gapfrom = ""

        try:
            if resuming and self._wip_dictionary is not None:
                dictionary = self._wip_dictionary
                all_candidates = self._wip_candidates
                root_entry_by_title = self._wip_root_entry_by_title
                total_redirects = self._wip_total_redirects
                start_gapfrom = self.resume_cursor
            else:
                self._set(status="counting")
                total_articles = client.count_pages()
                self._set(total_pages=total_articles, status="crawling")
                dictionary = Dictionary(name=self.dict_name, base_url=self.base_url)

            self._set(status="crawling", retry_info=None)

            for title, wikitext, redirect_titles in client.iter_content_pages(
                batch_size=BATCH_SIZE, start_gapfrom=start_gapfrom,
                on_retry=self._on_retry, request_delay=REQUEST_DELAY,
            ):
                if self._wait_while_paused():
                    break
                with self._lock:
                    if self.status != "crawling":
                        self.status = "crawling"
                    self.retry_info = None

                if wikitext is not None:
                    self._set(current_title=title)
                    section_tree = build_section_tree(wikitext)
                    article_url = client.article_url(title)
                    entries, candidates = build_entries_for_page(
                        title, section_tree, article_url,
                        apply_tense_fix=self.apply_tense_fix,
                        wikitext=wikitext,
                    )
                    for e in entries:
                        dictionary.add(e)
                    all_candidates.extend(candidates)

                    root_entry = next((e for e in entries if e.headword == title), None) or (entries[0] if entries else None)
                    if root_entry:
                        root_entry_by_title[title] = root_entry
                        for r in redirect_titles:
                            all_candidates.append((root_entry, r, PRIORITY_REDIRECT))
                            total_redirects += 1

                    with self._lock:
                        self.processed_pages += 1
                        self.entries_created = len(dictionary.entries)
                        self.redirect_pages = total_redirects
                else:
                    # Continuation batch: same page(s) as before, just more
                    # inbound redirects than fit in the first response.
                    root_entry = root_entry_by_title.get(title)
                    if root_entry:
                        for r in redirect_titles:
                            all_candidates.append((root_entry, r, PRIORITY_REDIRECT))
                            total_redirects += 1
                        with self._lock:
                            self.redirect_pages = total_redirects

            with self._lock:
                stopped = self.stop_requested

            # Resolve every alias candidate from every page together, so
            # the most confident source always wins regardless of the
            # (alphabetical) order pages happened to be crawled in.
            _resolve_alias_candidates(all_candidates, dictionary.entries)

            # "See More" - every entry lists the rest of its own page's
            # table of contents.
            attach_see_more_sections(dictionary.entries)

            final_status = "stopped" if stopped else "done"
            self._set(status=final_status, dictionary=dictionary, current_title="",
                      can_resume=False, retry_info=None)
        except TransientAPIError as exc:
            # The wiki kept failing even after automatic retries - save
            # everything collected so far so a manual Resume can pick up
            # from this exact page instead of starting the whole crawl
            # over.
            self._wip_dictionary = dictionary
            self._wip_candidates = all_candidates
            self._wip_root_entry_by_title = root_entry_by_title
            self._wip_total_redirects = total_redirects
            self.resume_cursor = client.current_gapfrom
            self._set(status="error", error=str(exc), can_resume=True, retry_info=None)
        except Exception as exc:  # noqa: BLE001
            self._wip_dictionary = dictionary
            self._wip_candidates = all_candidates
            self._wip_root_entry_by_title = root_entry_by_title
            self._wip_total_redirects = total_redirects
            self.resume_cursor = client.current_gapfrom
            can_resume = dictionary is not None and len(dictionary.entries) > 0
            self._set(status="error", error=str(exc), can_resume=can_resume, retry_info=None)


class CrawlManager:
    def __init__(self):
        self.jobs: dict[str, CrawlJob] = {}
        self._lock = threading.Lock()

    def create(self, base_url: str, dict_name: str, apply_tense_fix: bool = True) -> CrawlJob:
        job_id = str(int(time.time() * 1000))
        job = CrawlJob(job_id, base_url, dict_name, apply_tense_fix)
        with self._lock:
            self.jobs[job_id] = job
        job.start()
        return job

    def get(self, job_id: str) -> CrawlJob | None:
        return self.jobs.get(job_id)


manager = CrawlManager()

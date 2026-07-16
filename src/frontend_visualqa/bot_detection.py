"""Detect when the browser is being bot-blocked or the page fails silently.

Growing e-commerce and enterprise sites sit behind bot managers (Akamai,
Cloudflare, PerimeterX/HUMAN, DataDome, Imperva/Incapsula). When they decide a
session is automated they serve a challenge/deny page or simply block the
CDN/API calls the page needs, so it renders blank or half-broken. The Navigator
then flails — clicking, waiting, refreshing — until the step limit, wasting a
whole run on a page that will never work.

``BrowserActivityMonitor`` attaches lightweight Playwright listeners to a page
and records the console errors, failed requests, and main-document response
status. ``classify_block`` turns those signals plus the current page
title/text/URL into a single high-confidence reason string (or ``None``), which
the verifier uses to stop the claim early with a ``not_testable`` verdict.

Policy is deliberately conservative: only high-confidence signals stop a run
(challenge markers, a blocking HTTP status on the main document, or a failed
main-document navigation). Console errors and blocked sub-resources are captured
for visibility and corroboration but never abort on their own — normal sites
produce plenty of both.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import ConsoleMessage, Page, Request, Response

logger = logging.getLogger(__name__)

# HTTP statuses that, on the *main document*, almost always mean a block or
# rate-limit rather than an ordinary application error. 401 is intentionally
# excluded — legitimate auth-gated flows use it.
BLOCKING_STATUSES: frozenset[int] = frozenset({403, 429, 503})

# Substrings (matched case-insensitively against title + visible text + URL)
# that are specific to bot-challenge / access-denied pages. Kept narrow on
# purpose so ordinary pages that happen to mention "captcha" in a footer do not
# trip the detector.
CHALLENGE_MARKERS: tuple[str, ...] = (
    "challenge validation",
    "access denied",
    "you don't have permission to access",
    "checking your browser before",
    "just a moment...",
    "attention required! | cloudflare",
    "verify you are human",
    "verify you are a human",
    "please verify you are a human",
    "are you a robot",
    "unusual traffic from your",
    "pardon our interruption",
    "you have been blocked",
    "why have i been blocked",
    "enable javascript and cookies to continue",
    "additional security check is required",
    "ddos protection by",
    # NOTE: deliberately no bare "please enable cookies" — ordinary GDPR/consent
    # banners use that exact wording on healthy 200 pages. Cloudflare's real
    # challenge is already covered by the more specific
    # "enable javascript and cookies to continue" marker above.
)

# Substrings that only appear in challenge/interstitial URLs. Kept precise —
# bare vendor names (e.g. "distil") match unrelated paths like "distillery".
URL_MARKERS: tuple[str, ...] = (
    "geo.captcha-delivery.com",
    "/_incapsula_",
    "__cf_chl",
    "/cdn-cgi/challenge",
    "distil_r_captcha",
)

_MAX_RECORDS = 50


@dataclass
class BrowserActivityMonitor:
    """Accumulate console/network signals for one page across a claim.

    Listeners are attached once via :meth:`attach`; :meth:`reset` clears the
    accumulated signals at the start of each claim so failures from a prior
    claim on a reused session do not leak forward.
    """

    console_errors: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_RECORDS))
    failed_requests: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_RECORDS))
    blocking_responses: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_RECORDS))
    last_main_status: int | None = None
    last_main_url: str | None = None
    main_document_failure: str | None = None
    _page: Any = None

    def attach(self, page: Page) -> None:
        """Wire Playwright listeners. Safe to call once per page lifetime."""
        self._page = page
        page.on("console", self._on_console)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)

    def reset(self, *, keep_navigation: bool = False) -> None:
        """Clear accumulated signals.

        ``keep_navigation=True`` preserves the main-document status/failure (set
        by the claim's landing navigation, which happens before the verifier
        runs) while clearing the noisy per-claim deques — used at claim start on
        a reused session so a prior claim's console spam doesn't leak forward.
        """
        self.console_errors.clear()
        self.failed_requests.clear()
        self.blocking_responses.clear()
        if not keep_navigation:
            self.last_main_status = None
            self.last_main_url = None
            self.main_document_failure = None

    # --- listeners (sync; must never raise into Playwright's event loop) ---

    def _on_console(self, message: ConsoleMessage) -> None:
        try:
            if message.type != "error":
                return
            text = (message.text or "").strip()
            if not text:
                return
            self.console_errors.append(text[:300])
            logger.info("Browser console error: %s", text[:300])
        except Exception:  # pragma: no cover - defensive
            logger.debug("console listener failed", exc_info=True)

    def _on_request_failed(self, request: Request) -> None:
        try:
            failure = ""
            try:
                failure = (request.failure or "") if isinstance(request.failure, str) else ""
            except Exception:
                failure = ""
            entry = f"{request.method} {request.url} — {failure or 'failed'}"
            self.failed_requests.append(entry[:400])
            logger.info("Browser request failed: %s", entry[:400])
            if self._is_main_document(request):
                self.main_document_failure = failure or "navigation failed"
        except Exception:  # pragma: no cover - defensive
            logger.debug("requestfailed listener failed", exc_info=True)

    def _on_response(self, response: Response) -> None:
        try:
            request = response.request
            status = response.status
            if self._is_main_document(request):
                self.last_main_status = status
                self.last_main_url = response.url
                # A fresh successful main-document load clears a stale failure
                # flag (e.g. after the model navigates away from a broken page).
                if status < 400:
                    self.main_document_failure = None
            if status in BLOCKING_STATUSES:
                self.blocking_responses.append(f"HTTP {status} {response.url}"[:400])
        except Exception:  # pragma: no cover - defensive
            logger.debug("response listener failed", exc_info=True)

    def _is_main_document(self, request: Request) -> bool:
        try:
            if request.resource_type != "document":
                return False
            page = self._page
            return page is None or request.frame == page.main_frame
        except Exception:
            return False

    def summary(self) -> str:
        """Compact human-readable summary of captured failures, for findings/logs."""
        parts: list[str] = []
        if self.last_main_status is not None:
            parts.append(f"last main-document status HTTP {self.last_main_status}")
        if self.main_document_failure:
            parts.append(f"main-document navigation failure: {self.main_document_failure}")
        if self.blocking_responses:
            parts.append(f"{len(self.blocking_responses)} blocking response(s): " + "; ".join(list(self.blocking_responses)[:3]))
        if self.failed_requests:
            parts.append(f"{len(self.failed_requests)} failed request(s): " + "; ".join(list(self.failed_requests)[:3]))
        if self.console_errors:
            parts.append(f"{len(self.console_errors)} console error(s): " + "; ".join(list(self.console_errors)[:3]))
        return " | ".join(parts) if parts else "no console/network failures captured"


def classify_block(
    monitor: BrowserActivityMonitor,
    *,
    page_url: str | None = None,
    page_title: str | None = None,
    page_text: str | None = None,
) -> str | None:
    """Return a high-confidence bot-block reason, or ``None`` if the page looks fine.

    Only the strong signals stop a run; console errors / blocked sub-resources
    are corroboration surfaced via :meth:`BrowserActivityMonitor.summary`, not a
    trigger.
    """
    # A recorded main-document failure only blocks when the latest main-document
    # load did not itself succeed. On a reused session, reset(keep_navigation=True)
    # carries a prior claim's transient main-document failure forward; if this
    # claim's landing navigation then loaded cleanly (status < 400), that success
    # is the authoritative signal and the stale failure must not short-circuit an
    # otherwise usable page as not_testable.
    main_document_loaded_ok = monitor.last_main_status is not None and monitor.last_main_status < 400
    if monitor.main_document_failure and not main_document_loaded_ok:
        return (
            f"the page did not load — main-document navigation failed "
            f"({monitor.main_document_failure}), which usually indicates a network "
            f"block or bot challenge"
        )

    if monitor.last_main_status in BLOCKING_STATUSES:
        url = monitor.last_main_url or page_url or "the page"
        return (
            f"the site returned HTTP {monitor.last_main_status} on the main document "
            f"({url}) — typically a bot-block or rate-limit response"
        )

    haystack = " ".join(
        part.lower() for part in (page_url or "", page_title or "", page_text or "") if part
    )
    if haystack:
        for marker in CHALLENGE_MARKERS:
            if marker in haystack:
                return f"the page shows a bot-challenge marker ({marker!r})"
    url_l = (page_url or "").lower()
    for marker in URL_MARKERS:
        if marker in url_l:
            return f"the page URL matches a bot-challenge pattern ({marker!r})"

    return None

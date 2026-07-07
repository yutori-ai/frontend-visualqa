from __future__ import annotations

from frontend_visualqa.bot_detection import (
    BLOCKING_STATUSES,
    BrowserActivityMonitor,
    classify_block,
)


class _FakePage:
    def __init__(self) -> None:
        self.main_frame = object()


class _FakeRequest:
    def __init__(self, *, resource_type="document", frame=None, method="GET", url="http://x/", failure=None):
        self.resource_type = resource_type
        self.frame = frame
        self.method = method
        self.url = url
        self.failure = failure


class _FakeResponse:
    def __init__(self, *, status, url, request):
        self.status = status
        self.url = url
        self.request = request


class _FakeConsole:
    def __init__(self, *, type, text):
        self.type = type
        self.text = text


def _monitor_with_page():
    m = BrowserActivityMonitor()
    m._page = _FakePage()
    return m


# --- classify_block: the decision logic --------------------------------------


def test_clean_page_is_not_blocked():
    assert classify_block(BrowserActivityMonitor(), page_title="Uline Boxes", page_text="Add to Cart") is None


def test_challenge_marker_in_text_blocks():
    reason = classify_block(BrowserActivityMonitor(), page_title="", page_text="Challenge Validation — please wait")
    assert reason and "challenge validation" in reason


def test_challenge_marker_is_case_insensitive():
    reason = classify_block(BrowserActivityMonitor(), page_title="PARDON OUR INTERRUPTION", page_text="")
    assert reason and "pardon our interruption" in reason


def test_url_marker_blocks():
    reason = classify_block(BrowserActivityMonitor(), page_url="https://geo.captcha-delivery.com/captcha/?x=1")
    assert reason and "geo.captcha-delivery.com" in reason


def test_blocking_status_on_main_document_blocks():
    m = BrowserActivityMonitor()
    m.last_main_status = 429
    m.last_main_url = "https://shop.example/cart"
    reason = classify_block(m)
    assert reason and "429" in reason


def test_non_blocking_status_does_not_block():
    m = BrowserActivityMonitor()
    m.last_main_status = 200
    assert classify_block(m, page_title="Home", page_text="Welcome") is None


def test_failed_main_navigation_blocks():
    m = BrowserActivityMonitor()
    m.main_document_failure = "net::ERR_CONNECTION_RESET"
    reason = classify_block(m)
    assert reason and "did not load" in reason


def test_gdpr_cookie_banner_does_not_trigger_block():
    """"please enable cookies" wording on a normal 200 page must not stop the run."""
    banner = "We value your privacy. Please enable cookies to continue using our site."
    assert classify_block(BrowserActivityMonitor(), page_title="Acme Store", page_text=banner) is None


def test_distil_substring_in_unrelated_url_does_not_block():
    """Bare 'distil' would match 'distillery'; the marker must be precise."""
    assert classify_block(BrowserActivityMonitor(), page_url="https://distillery.example/whiskey") is None
    # ...but a genuine Distil challenge path still trips.
    reason = classify_block(BrowserActivityMonitor(), page_url="https://x.example/distil_r_captcha/")
    assert reason and "distil_r_captcha" in reason


def test_console_errors_alone_do_not_block():
    """Conservative policy: sub-resource/console noise must not stop a run."""
    m = BrowserActivityMonitor()
    m.console_errors.append("Failed to load resource: net::ERR_BLOCKED_BY_CLIENT")
    m.failed_requests.append("GET https://cdn.example/a.js — net::ERR_FAILED")
    assert classify_block(m, page_title="Uline", page_text="Corrugated Boxes") is None


# --- monitor: listener bookkeeping -------------------------------------------


def test_response_records_main_document_status_and_blocking():
    m = _monitor_with_page()
    req = _FakeRequest(resource_type="document", frame=m._page.main_frame, url="https://x/p")
    m._on_response(_FakeResponse(status=403, url="https://x/p", request=req))
    assert m.last_main_status == 403
    assert m.blocking_responses  # 403 is a blocking status
    assert 403 in BLOCKING_STATUSES


def test_subresource_blocking_status_not_treated_as_main_document():
    m = _monitor_with_page()
    sub = _FakeRequest(resource_type="script", frame=m._page.main_frame, url="https://cdn/x.js")
    m._on_response(_FakeResponse(status=403, url="https://cdn/x.js", request=sub))
    assert m.last_main_status is None  # not a document
    assert m.blocking_responses  # still recorded for the summary


def test_successful_main_navigation_clears_stale_failure():
    m = _monitor_with_page()
    m.main_document_failure = "net::ERR_FAILED"
    req = _FakeRequest(resource_type="document", frame=m._page.main_frame, url="https://x/ok")
    m._on_response(_FakeResponse(status=200, url="https://x/ok", request=req))
    assert m.main_document_failure is None


def test_request_failed_on_main_document_sets_failure():
    m = _monitor_with_page()
    req = _FakeRequest(resource_type="document", frame=m._page.main_frame, url="https://x/", failure="net::ERR_TIMED_OUT")
    m._on_request_failed(req)
    assert m.main_document_failure == "net::ERR_TIMED_OUT"
    assert m.failed_requests


def test_console_only_records_errors():
    m = _monitor_with_page()
    m._on_console(_FakeConsole(type="log", text="hello"))
    m._on_console(_FakeConsole(type="error", text="Boom"))
    assert list(m.console_errors) == ["Boom"]


def test_reset_keep_navigation_preserves_status_but_clears_noise():
    m = _monitor_with_page()
    m.last_main_status = 403
    m.main_document_failure = "net::ERR_FAILED"
    m.console_errors.append("x")
    m.failed_requests.append("y")
    m.reset(keep_navigation=True)
    assert m.last_main_status == 403
    assert m.main_document_failure == "net::ERR_FAILED"
    assert not m.console_errors and not m.failed_requests


def test_full_reset_clears_everything():
    m = _monitor_with_page()
    m.last_main_status = 403
    m.console_errors.append("x")
    m.reset()
    assert m.last_main_status is None and not m.console_errors

"""Tests for the shared safe_method_call / safe_async_method_call / safe_callback_call utilities."""

import logging

import pytest

from frontend_visualqa.utils import (
    safe_async_method_call,
    safe_callback_call,
    safe_method_call,
)


def test_sync_none_target_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        safe_method_call(None, "any_method", "arg1", key="val")

    assert not caplog.records


def test_sync_missing_method_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    class Stub:
        pass

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        safe_method_call(Stub(), "nonexistent_method")

    assert not caplog.records


def test_sync_non_callable_attribute_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    class Stub:
        some_method = 42  # not callable

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        safe_method_call(Stub(), "some_method")

    assert not caplog.records


def test_sync_successful_call_forwards_args_and_kwargs() -> None:
    captured: dict = {}

    class Stub:
        def do_thing(self, x: int, tag: str = "") -> None:
            captured["x"] = x
            captured["tag"] = tag

    safe_method_call(Stub(), "do_thing", 42, tag="hello")
    assert captured == {"x": 42, "tag": "hello"}


def test_sync_keyword_named_label_is_forwarded_to_target_method() -> None:
    captured: dict[str, str] = {}

    class Stub:
        def set_status(self, *, label: str) -> None:
            captured["label"] = label

    safe_method_call(Stub(), "set_status", label="Analyzing", log_label="Hook")

    assert captured == {"label": "Analyzing"}


def test_sync_exception_is_swallowed_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    class Stub:
        def boom(self) -> None:
            raise RuntimeError("kaboom")

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        safe_method_call(Stub(), "boom", log_label="Hook")

    assert any("Hook" in r.message and "boom" in r.message for r in caplog.records)


def test_sync_default_label_uses_type_name(caplog: pytest.LogCaptureFixture) -> None:
    class MyHook:
        def fail(self) -> None:
            raise ValueError("oops")

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        safe_method_call(MyHook(), "fail")

    assert any("MyHook" in r.message and "fail" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_none_target_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    """Calling with target=None should do nothing and not raise."""

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        await safe_async_method_call(None, "any_method", "arg1", key="val")

    assert not caplog.records


@pytest.mark.asyncio
async def test_missing_method_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    """Target without the named method should be a silent no-op."""

    class Stub:
        pass

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        await safe_async_method_call(Stub(), "nonexistent_method")

    assert not caplog.records


@pytest.mark.asyncio
async def test_non_callable_attribute_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    """Target with a non-callable attribute of the same name should be a no-op."""

    class Stub:
        some_method = 42  # not callable

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        await safe_async_method_call(Stub(), "some_method")

    assert not caplog.records


@pytest.mark.asyncio
async def test_successful_call_forwards_args_and_kwargs() -> None:
    """Arguments and keyword arguments are forwarded to the method."""
    captured: dict = {}

    class Stub:
        async def do_thing(self, x: int, tag: str = "") -> None:
            captured["x"] = x
            captured["tag"] = tag

    await safe_async_method_call(Stub(), "do_thing", 42, tag="hello")
    assert captured == {"x": 42, "tag": "hello"}


@pytest.mark.asyncio
async def test_keyword_named_label_is_forwarded_to_target_method() -> None:
    """Forwarded kwargs should remain available even when named ``label``."""

    captured: dict[str, str] = {}

    class Stub:
        async def set_status(self, *, label: str) -> None:
            captured["label"] = label

    await safe_async_method_call(Stub(), "set_status", label="Analyzing", log_label="Overlay")

    assert captured == {"label": "Analyzing"}


@pytest.mark.asyncio
async def test_exception_is_swallowed_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Method exceptions are caught and logged at DEBUG, not propagated."""

    class Stub:
        async def boom(self) -> None:
            raise RuntimeError("kaboom")

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        await safe_async_method_call(Stub(), "boom", log_label="Overlay")

    assert any("Overlay" in r.message and "boom" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_default_label_uses_type_name(caplog: pytest.LogCaptureFixture) -> None:
    """When no label is given, the log message uses the target's type name."""

    class MyOverlay:
        async def fail(self) -> None:
            raise ValueError("oops")

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        await safe_async_method_call(MyOverlay(), "fail")

    assert any("MyOverlay" in r.message and "fail" in r.message for r in caplog.records)


def test_safe_callback_call_none_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        safe_callback_call(None, 1, 2, log_label="Progress")

    assert not caplog.records


def test_safe_callback_call_forwards_args_and_kwargs() -> None:
    captured: dict = {}

    def cb(index: int, claim: str, *, tag: str = "") -> None:
        captured["index"] = index
        captured["claim"] = claim
        captured["tag"] = tag

    safe_callback_call(cb, 3, "the claim", tag="hello")
    assert captured == {"index": 3, "claim": "the claim", "tag": "hello"}


def test_safe_callback_call_swallows_exception_and_logs_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def cb() -> None:
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.WARNING, logger="frontend_visualqa.utils"):
        safe_callback_call(cb, log_label="Claim start callback for claim 7")

    matching = [r for r in caplog.records if "Claim start callback for claim 7" in r.message]
    assert matching, "expected log message containing the supplied label"
    assert all(r.levelno == logging.WARNING for r in matching)
    assert any(r.exc_info is not None for r in matching)


def test_safe_callback_call_default_label(caplog: pytest.LogCaptureFixture) -> None:
    def cb() -> None:
        raise ValueError("oops")

    with caplog.at_level(logging.WARNING, logger="frontend_visualqa.utils"):
        safe_callback_call(cb)

    assert any("Callback" in r.message and "failed" in r.message for r in caplog.records)


def test_safe_callback_call_uses_caller_logger_when_supplied(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caller_logger = logging.getLogger("frontend_visualqa.runner")

    def cb() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger="frontend_visualqa.runner"):
        safe_callback_call(cb, log_label="Claim callback", log=caller_logger)

    matching = [r for r in caplog.records if "Claim callback" in r.message]
    assert matching, "expected a log record under the caller-supplied logger"
    assert all(r.name == "frontend_visualqa.runner" for r in matching)

"""Tests for the shared safe_async_method_call utility."""

import logging

import pytest

from frontend_visualqa.utils import safe_async_method_call


@pytest.mark.asyncio
async def test_none_target_is_noop() -> None:
    """Calling with target=None should do nothing and not raise."""
    await safe_async_method_call(None, "any_method", "arg1", key="val")


@pytest.mark.asyncio
async def test_missing_method_is_noop() -> None:
    """Target without the named method should be a silent no-op."""

    class Stub:
        pass

    await safe_async_method_call(Stub(), "nonexistent_method")


@pytest.mark.asyncio
async def test_non_callable_attribute_is_noop() -> None:
    """Target with a non-callable attribute of the same name should be a no-op."""

    class Stub:
        some_method = 42  # not callable

    await safe_async_method_call(Stub(), "some_method")


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
async def test_exception_is_swallowed_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Method exceptions are caught and logged at DEBUG, not propagated."""

    class Stub:
        async def boom(self) -> None:
            raise RuntimeError("kaboom")

    with caplog.at_level(logging.DEBUG, logger="frontend_visualqa.utils"):
        await safe_async_method_call(Stub(), "boom", label="Overlay")

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

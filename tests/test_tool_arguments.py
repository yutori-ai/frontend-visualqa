from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend_visualqa.errors import BrowserActionError
from frontend_visualqa.tool_arguments import parse_tool_arguments


def _tool_call(arguments: object) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(arguments=arguments))


def test_parse_tool_arguments_accepts_dict_arguments() -> None:
    arguments = {"text": "ATMOS"}

    assert parse_tool_arguments(_tool_call(arguments)) == arguments


def test_parse_tool_arguments_parses_json_object_arguments() -> None:
    parsed = parse_tool_arguments(_tool_call('{"text":"ATMOS"}'))

    assert parsed == {"text": "ATMOS"}


def test_parse_tool_arguments_rejects_invalid_json() -> None:
    with pytest.raises(BrowserActionError, match="tool arguments were not valid JSON"):
        parse_tool_arguments(_tool_call("{invalid"))


def test_parse_tool_arguments_rejects_non_object_json() -> None:
    with pytest.raises(BrowserActionError, match="tool arguments must decode to an object"):
        parse_tool_arguments(_tool_call('["ATMOS"]'))

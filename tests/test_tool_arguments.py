from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend_visualqa.errors import BrowserActionError
from frontend_visualqa.tool_arguments import parse_tool_arguments, tool_call_name


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


def test_tool_call_name_reads_function_name() -> None:
    tool_call = SimpleNamespace(function=SimpleNamespace(name="click"))

    assert tool_call_name(tool_call) == "click"


def test_tool_call_name_falls_back_to_top_level_name() -> None:
    flat = SimpleNamespace(name="scroll")

    assert tool_call_name(flat) == "scroll"


def test_tool_call_name_defaults_to_empty_when_missing() -> None:
    assert tool_call_name(SimpleNamespace()) == ""

"""CLI argument parsing tests — especially that --verbose works in either position."""

from __future__ import annotations

import pytest

from coursectl.cli import build_parser


def parse(argv: list[str]):
    return build_parser().parse_args(argv)


def test_verbose_before_subcommand():
    args = parse(["--verbose", "login", "abc123"])
    assert args.verbose is True
    assert args.username == "abc123"


def test_verbose_after_subcommand():
    args = parse(["login", "abc123", "--verbose"])
    assert args.verbose is True
    assert args.username == "abc123"


def test_verbose_absent_defaults_false():
    args = parse(["login", "abc123"])
    assert args.verbose is False


def test_verbose_still_false_for_other_commands():
    args = parse(["accounts"])
    assert args.verbose is False


def test_login_strategy_validation():
    with pytest.raises(SystemExit):
        parse(["login", "abc123", "--strategy", "telepathy"])
    assert parse(["login", "abc123", "--strategy", "mobile_sso"]).strategy == "mobile_sso"


def test_every_subcommand_parses():
    cases = [
        ["serve", "--port", "8787"],
        ["mcp", "--account", "abc123"],
        ["mcp", "--account", "abc123", "--http", "--port", "8790"],
        ["accounts", "--json"],
        ["revoke", "abc123"],
        ["check", "--account", "abc123"],
    ]
    for argv in cases:
        args = parse(argv)
        assert callable(args.func), argv


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse(["--help"])
    assert excinfo.value.code == 0

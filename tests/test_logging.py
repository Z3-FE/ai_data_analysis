import logging

from app.core.logging import ConsoleFormatter


def test_info_is_cyan_without_changing_record():
    record = logging.LogRecord("test", logging.INFO, "", 1, "value=%s", (42,), None)
    formatter = ConsoleFormatter("%(levelname)s | %(message)s", use_color=True)
    assert formatter.format(record) == "\033[36mINFO | value=42\033[0m"
    assert record.msg == "value=%s"
    assert record.levelname == "INFO"


def test_disabled_color_keeps_plain_text():
    record = logging.LogRecord("test", logging.INFO, "", 1, "hello", (), None)
    formatter = ConsoleFormatter("%(message)s", use_color=False)
    assert formatter.format(record) == "hello"


def test_other_levels_are_unchanged():
    record = logging.LogRecord("test", logging.ERROR, "", 1, "error", (), None)
    formatter = ConsoleFormatter("%(levelname)s | %(message)s", use_color=True)
    assert formatter.format(record) == "ERROR | error"

"""The typed failure contract every DBC caller sees.

A caller must never have to catch ``FileNotFoundError``, ``UnicodeDecodeError``,
a ``cantools`` exception, a bare ``ValueError`` or a ``textparser`` error. Each
of those becomes one of these, carrying the same five structured fields SPEC §38
requires of every diagnosable boundary failure.
"""

import pytest
from canx.dbc.errors import (
    DbcDecodeError,
    DbcError,
    DbcFileNotFoundError,
    DbcModelError,
    DbcParseError,
    DbcReadError,
    DbcUnsupportedFormatError,
)

CONCRETE_ERRORS = (
    (DbcFileNotFoundError, "dbc.file_not_found", False),
    (DbcReadError, "dbc.read_failed", True),
    (DbcUnsupportedFormatError, "dbc.unsupported_format", False),
    (DbcDecodeError, "dbc.decode_failed", False),
    (DbcParseError, "dbc.parse_failed", False),
    (DbcModelError, "dbc.invalid_model", False),
)


@pytest.mark.parametrize(("error_type", "code", "recoverable"), CONCRETE_ERRORS)
def test_every_dbc_failure_carries_the_shared_five_field_envelope(
    error_type: type[DbcError], code: str, recoverable: bool
) -> None:
    error = error_type("The DBC source could not be read.", details={"source_name": "x.dbc"})

    assert isinstance(error, DbcError)
    assert error.code == code
    assert error.source == "dbc"
    assert error.recoverable is recoverable
    assert error.message == "The DBC source could not be read."
    assert error.details == {"source_name": "x.dbc"}
    assert str(error) == "The DBC source could not be read."


def test_the_base_error_uses_a_generic_dbc_code() -> None:
    error = DbcError("Something went wrong.")

    assert error.code == "dbc.error"
    assert error.source == "dbc"
    assert error.recoverable is False
    assert error.details == {}


def test_details_default_to_a_private_copy() -> None:
    supplied = {"source_name": "x.dbc"}
    error = DbcError("boom", details=supplied)

    supplied["source_name"] = "y.dbc"

    assert error.details == {"source_name": "x.dbc"}


def test_a_read_failure_is_recoverable_while_a_decode_failure_is_not() -> None:
    """A locked file may be readable later; malformed bytes will not get better."""
    assert DbcReadError("locked").recoverable is True
    assert DbcDecodeError("not utf-8").recoverable is False


def test_every_concrete_failure_is_distinguishable_by_code() -> None:
    codes = {code for _type, code, _recoverable in CONCRETE_ERRORS}

    assert len(codes) == len(CONCRETE_ERRORS)
    assert codes == {
        "dbc.file_not_found",
        "dbc.read_failed",
        "dbc.unsupported_format",
        "dbc.decode_failed",
        "dbc.parse_failed",
        "dbc.invalid_model",
    }


def test_a_concrete_failure_can_override_its_code_for_a_specific_diagnosis() -> None:
    error = DbcParseError("too many messages", code="dbc.parse_failed.too_many_messages")

    assert error.code == "dbc.parse_failed.too_many_messages"
    assert error.source == "dbc"


def test_the_human_message_carries_no_code_or_source_noise() -> None:
    """``code``, ``source`` and ``recoverable`` are fields, not text.

    A caller that renders the message to an operator must not have to strip an
    error taxonomy back out of it.
    """
    error = DbcModelError("The DBC document contradicts the CAN-X model.")

    assert isinstance(error, Exception)
    assert str(error) == "The DBC document contradicts the CAN-X model."
    assert "dbc." not in str(error)

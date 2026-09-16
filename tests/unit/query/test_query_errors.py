"""Behavior tests for the typed query failure contract."""

import pytest
from canx.query.errors import (
    QueryDataUnavailableError,
    QueryError,
    QueryExecutionError,
    QueryIntegrityError,
    QuerySessionError,
    QueryValidationError,
)

ALL_ERRORS = (
    QueryValidationError,
    QuerySessionError,
    QueryDataUnavailableError,
    QueryIntegrityError,
    QueryExecutionError,
)


@pytest.mark.parametrize("error_type", ALL_ERRORS)
def test_every_query_error_carries_the_structured_contract(
    error_type: type[QueryError],
) -> None:
    error = error_type("boom", details={"field": "x"})

    assert isinstance(error, QueryError)
    assert not isinstance(error, ValueError)
    assert error.message == "boom"
    assert error.details == {"field": "x"}
    assert error.source == "query"
    assert isinstance(error.code, str)
    assert error.code


def test_the_query_error_family_is_closed_and_flat() -> None:
    for error_type in ALL_ERRORS:
        assert issubclass(error_type, QueryError)
        assert error_type is not QueryError


def test_every_query_error_has_its_own_default_code() -> None:
    codes = {error_type("x").code for error_type in ALL_ERRORS}

    assert len(codes) == len(ALL_ERRORS)
    assert all(code.startswith("query.") for code in codes)


def test_recoverable_reflects_the_documented_semantics() -> None:
    assert QueryDataUnavailableError("x").recoverable is True
    assert QueryExecutionError("x").recoverable is True
    assert QueryValidationError("x").recoverable is False
    assert QuerySessionError("x").recoverable is False
    assert QueryIntegrityError("x").recoverable is False


def test_details_defaults_to_an_empty_mapping_and_is_copied() -> None:
    source = {"field": "x"}

    error = QueryValidationError("x", details=source)
    source["field"] = "mutated"

    assert QueryValidationError("x").details == {}
    assert error.details == {"field": "x"}


def test_a_query_error_can_chain_the_engine_failure_it_translated() -> None:
    original = OSError("disk went away")

    with pytest.raises(QueryExecutionError) as info:
        try:
            raise original
        except OSError as error:
            raise QueryExecutionError("engine failed") from error

    assert info.value.__cause__ is original
    assert info.value.code == "query.execution_failed"

from datetime import datetime, timedelta, timezone

import pytest

from paios_ingestion import contract


def deadline(seconds: int = 300) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def limits():
    return contract.test_limits(deadline())


def limited(**overrides):
    base = contract.test_limits(deadline()).__dict__ | overrides
    return contract.Limits(**base)


def error_of(excinfo) -> dict:
    error = excinfo.value.error
    contract.validate("Error", error)
    return error

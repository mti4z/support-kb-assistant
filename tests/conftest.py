import pytest

from app.ratelimit import window


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Each test starts with empty usage counters."""
    window.clear()
    yield
    window.clear()

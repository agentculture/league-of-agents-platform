"""Tests for :mod:`league_site.capacity.config`."""

from __future__ import annotations

import pytest

from league_site.capacity.config import CEILING_USD, ENV_PREFIX, CapacityConfig


def test_default_matches_the_documented_caps() -> None:
    config = CapacityConfig.default()
    assert config.max_concurrent_matches == 50
    assert config.max_stored_matches == 500
    assert config.max_match_age_days_hot == 3
    assert config.max_archive_age_days == 180
    assert config.ceiling_usd == 20


def test_ceiling_usd_field_matches_the_module_constant() -> None:
    assert CapacityConfig.default().ceiling_usd == CEILING_USD
    assert CEILING_USD == 20


def test_env_prefix_is_league_capacity() -> None:
    assert ENV_PREFIX == "LEAGUE_CAPACITY_"


def test_from_env_with_no_matching_vars_returns_the_default() -> None:
    assert CapacityConfig.from_env({}) == CapacityConfig.default()


def test_from_env_overrides_each_capacity_field() -> None:
    env = {
        "LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES": "10",
        "LEAGUE_CAPACITY_MAX_STORED_MATCHES": "100",
        "LEAGUE_CAPACITY_MAX_MATCH_AGE_DAYS_HOT": "1",
        "LEAGUE_CAPACITY_MAX_ARCHIVE_AGE_DAYS": "30",
    }
    config = CapacityConfig.from_env(env)
    assert config.max_concurrent_matches == 10
    assert config.max_stored_matches == 100
    assert config.max_match_age_days_hot == 1
    assert config.max_archive_age_days == 30
    # Untouched by the env dict at all: still the committed ceiling.
    assert config.ceiling_usd == 20


def test_from_env_ignores_an_empty_string_value() -> None:
    config = CapacityConfig.from_env({"LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES": ""})
    assert config.max_concurrent_matches == CapacityConfig.default().max_concurrent_matches


def test_from_env_never_overrides_ceiling_usd() -> None:
    """ceiling_usd documents the fixed design ceiling; it is not a per-deploy dial."""
    config = CapacityConfig.from_env({"LEAGUE_CAPACITY_CEILING_USD": "999"})
    assert config.ceiling_usd == 20


def test_from_env_rejects_a_non_integer_override_and_names_the_variable() -> None:
    with pytest.raises(ValueError, match="LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES"):
        CapacityConfig.from_env({"LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES": "not-a-number"})


def test_from_env_defaults_to_reading_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_CAPACITY_MAX_STORED_MATCHES", "42")
    config = CapacityConfig.from_env()
    assert config.max_stored_matches == 42


@pytest.mark.parametrize(
    "field_name",
    [
        "max_concurrent_matches",
        "max_stored_matches",
        "max_match_age_days_hot",
        "max_archive_age_days",
    ],
)
def test_zero_or_negative_caps_are_rejected(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        CapacityConfig(**{field_name: 0})
    with pytest.raises(ValueError, match=field_name):
        CapacityConfig(**{field_name: -1})


def test_config_is_immutable() -> None:
    config = CapacityConfig.default()
    with pytest.raises(AttributeError):
        config.max_concurrent_matches = 1  # type: ignore[misc]

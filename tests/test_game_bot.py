"""Unit tests for :mod:`league_site.game.bot` — the house side's harness config.

Pure data-shaping tests, no subprocess involved: the module only translates a
mode's declared ``bot_policy`` labels into the ``league harness run`` resume
config the adapter writes into the match workdir.
``tests/test_game_adapter_fake.py`` proves the adapter actually invokes
``harness run`` with that config; ``tests/test_game_real_cli.py`` proves the
real CLI's harness then stages non-hold house orders.
"""

from __future__ import annotations

import pytest

from league_site.game import bot
from league_site.game.modes import SOLO_VS_BOT, TEAM_VS_TEAM, LaunchMode, TeamSpec

# -- driver_spec: policy label -> harness driver spec -------------------------


def test_driver_spec_maps_the_greedy_policy_to_the_in_harness_bot_driver() -> None:
    assert bot.driver_spec("bot:greedy") == {"type": "bot"}


def test_driver_spec_maps_a_bot_file_policy_to_the_bot_file_driver() -> None:
    assert bot.driver_spec("bot-file:rusher") == {"type": "bot-file", "strategy": "rusher"}


@pytest.mark.parametrize("policy", ["", "bot", "bot:", "bot:random", "bot-file:", "greedy"])
def test_driver_spec_rejects_unknown_policy_labels(policy: str) -> None:
    with pytest.raises(ValueError, match="bot policy"):
        bot.driver_spec(policy)


# -- driven_bot_teams ----------------------------------------------------------


def test_driven_bot_teams_returns_only_bot_teams_with_a_policy() -> None:
    driven = bot.driven_bot_teams(SOLO_VS_BOT)
    assert tuple(t.team_id for t in driven) == ("house",)
    assert bot.driven_bot_teams(TEAM_VS_TEAM) == ()


def test_driven_bot_teams_skips_a_policy_less_bot_team() -> None:
    mode = _mode_with(
        TeamSpec(team_id="p", driver_kind="stateless"),
        TeamSpec(team_id="idle-house", driver_kind="bot", is_bot=True, bot_policy=None),
    )
    assert bot.driven_bot_teams(mode) == ()


# -- harness_config: the `league harness run` resume config --------------------


def test_harness_config_builds_a_one_round_resume_config_for_the_bot_side() -> None:
    config = bot.harness_config(SOLO_VS_BOT, match_id="m-abc", scenario_id="skirmish-2")
    assert config == {
        "match": {"scenario": "skirmish-2", "id": "m-abc"},
        "teams": [{"id": "house", "driver": {"type": "bot"}}],
        "max_rounds": 1,
    }


def test_harness_config_never_includes_participant_controlled_teams() -> None:
    config = bot.harness_config(SOLO_VS_BOT, match_id="m-abc", scenario_id="skirmish-1")
    assert [t["id"] for t in config["teams"]] == ["house"]


def test_harness_config_raises_when_the_mode_has_no_driven_bot_team() -> None:
    with pytest.raises(ValueError, match="no bot team"):
        bot.harness_config(TEAM_VS_TEAM, match_id="m-abc", scenario_id="skirmish-1")


def test_harness_config_supports_multiple_driven_bot_teams() -> None:
    mode = _mode_with(
        TeamSpec(team_id="p", driver_kind="stateless"),
        TeamSpec(team_id="h1", driver_kind="bot", is_bot=True, bot_policy="bot:greedy"),
        TeamSpec(team_id="h2", driver_kind="bot", is_bot=True, bot_policy="bot-file:rusher"),
    )
    config = bot.harness_config(mode, match_id="m-2", scenario_id="skirmish-1")
    assert config["teams"] == [
        {"id": "h1", "driver": {"type": "bot"}},
        {"id": "h2", "driver": {"type": "bot-file", "strategy": "rusher"}},
    ]


def _mode_with(*teams: TeamSpec) -> LaunchMode:
    return LaunchMode(
        name="test-mode",
        game_mode="competitive",
        scenario_id="skirmish-1",
        seed=1,
        expected_participants=1,
        teams=teams,
    )

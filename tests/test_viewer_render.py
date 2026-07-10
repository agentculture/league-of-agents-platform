"""Unit tests for :mod:`league_site.viewer.render`.

Covers the page-model/HTML-fragment layer directly (no WSGI involved): the
header shows participants (with model/provider chips) and, once completed,
scores + a result + profile links; turn messages render as markdown while
hostile content anywhere (message, display name, opaque orders payload) is
always escaped, never emitted as a live tag.
"""

from __future__ import annotations

from league_site.matches import AgentIdentity, Participant, ParticipantKind
from league_site.profiles.data import identity_slug
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from league_site.ratings.system import IntegerEloRatingSystem, RatingIdentity, outcome_from_match
from league_site.viewer.render import build_page_model, render_page_body
from tests._viewer_support import start_match

HOSTILE = """<script>alert('x')</script>&"'"""


def test_in_progress_match_has_no_scores_no_winner_and_no_profile_links() -> None:
    match, _ = start_match()
    model = build_page_model(match)
    assert model.is_live is True
    assert model.status == "active"
    assert model.winner_participant_id is None
    for participant in model.participants:
        assert participant.score is None
        assert participant.profile_href is None


def test_completed_match_shows_scores_winner_and_profile_links() -> None:
    match, engine = start_match()
    human, agent = match.participants
    match.take_turn(engine, human.participant_id, {"delta": 3})
    match.take_turn(engine, agent.participant_id, {"delta": 7})
    match.complete(engine)

    model = build_page_model(match)
    assert model.is_live is False
    assert model.status == "completed"
    assert model.winner_participant_id == agent.participant_id

    by_id = {p.participant_id: p for p in model.participants}
    assert by_id[human.participant_id].score == 3.0
    assert by_id[agent.participant_id].score == 7.0
    assert (
        by_id[human.participant_id].profile_href
        == f"/profiles/{identity_slug(RatingIdentity.from_participant(human))}"
    )
    assert (
        by_id[agent.participant_id].profile_href
        == f"/profiles/{identity_slug(RatingIdentity.from_participant(agent))}"
    )


def test_completed_match_result_summary_renders_as_markdown() -> None:
    match, engine = start_match()
    human, agent = match.participants
    match.take_turn(engine, human.participant_id, {"delta": 3})
    match.take_turn(engine, agent.participant_id, {"delta": 7})
    match.complete(engine)
    match.result.summary = "**gg** well played"

    model = build_page_model(match)
    assert model.summary_html is not None
    assert "<strong>gg</strong>" in model.summary_html
    assert "<strong>gg</strong>" in render_page_body(model)


def test_completed_match_with_no_winner_shows_a_draw_placeholder() -> None:
    match, engine = start_match()
    human, agent = match.participants
    match.take_turn(engine, human.participant_id, {"delta": 1})
    match.complete(engine)
    match.result.winner_participant_id = None

    model = build_page_model(match)
    assert model.winner_participant_id is None
    html_body = render_page_body(model)
    assert "No winner recorded (draw or unscored)." in html_body


def test_agent_participant_carries_model_and_provider_chips() -> None:
    match, _ = start_match()
    model = build_page_model(match)
    agent_view = next(p for p in model.participants if p.kind == "agent")
    assert agent_view.model_html == "claude-sonnet-5"
    assert agent_view.provider_html == "anthropic"
    human_view = next(p for p in model.participants if p.kind == "human")
    assert human_view.model_html is None
    assert human_view.provider_html is None


def test_ledger_store_adds_current_rating_to_each_participant() -> None:
    match, _ = start_match()
    human, agent = match.participants
    ledger = InMemoryRatingLedgerStore()
    system = IntegerEloRatingSystem()
    completed_match, engine = start_match(match_id="m-rated")
    completed_match.take_turn(engine, human.participant_id, {"delta": 5})
    completed_match.complete(engine)
    ledger.record_match(outcome_from_match(completed_match), system)

    model = build_page_model(match, ledger)
    by_id = {p.participant_id: p for p in model.participants}
    assert isinstance(by_id[human.participant_id].rating, int)
    assert isinstance(by_id[agent.participant_id].rating, int)
    assert f"rating {by_id[human.participant_id].rating}" in render_page_body(model)


def test_build_page_model_without_ledger_store_leaves_rating_none() -> None:
    match, _ = start_match()
    model = build_page_model(match)
    for participant in model.participants:
        assert participant.rating is None


def test_turn_message_renders_markdown_bold_and_code() -> None:
    match, engine = start_match()
    human, _agent = match.participants
    match.take_turn(engine, human.participant_id, {"message": "**bold** and `code`"})

    model = build_page_model(match)
    html_body = render_page_body(model)
    assert "<strong>bold</strong>" in html_body
    assert "<code>code</code>" in html_body


def test_hostile_message_content_is_escaped_not_executed() -> None:
    match, engine = start_match()
    human, _agent = match.participants
    match.take_turn(engine, human.participant_id, {"message": HOSTILE})

    model = build_page_model(match)
    html_body = render_page_body(model)
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_hostile_display_name_is_escaped_in_participants_and_turn_attribution() -> None:
    hostile_human = Participant(
        display_name=HOSTILE, kind=ParticipantKind.HUMAN, participant_id="p-hostile"
    )
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    match, engine = start_match(participants=(hostile_human, agent))
    match.take_turn(engine, hostile_human.participant_id, {"delta": 1})

    model = build_page_model(match)
    html_body = render_page_body(model)
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_orders_payload_is_rendered_as_escaped_json_not_markdown() -> None:
    match, engine = start_match()
    human, _agent = match.participants
    match.take_turn(
        engine,
        human.participant_id,
        {"delta": 1, "orders": ["**not markdown**", HOSTILE]},
    )

    model = build_page_model(match)
    html_body = render_page_body(model)
    # The orders payload is JSON-escaped, so markdown syntax inside it must
    # NOT turn into a <strong> tag, and hostile content must stay escaped.
    assert "<strong>not markdown</strong>" not in html_body
    assert "&lt;script&gt;" in html_body
    assert "<script>" not in html_body


def test_action_only_message_has_no_empty_orders_block() -> None:
    match, engine = start_match()
    human, _agent = match.participants
    match.take_turn(engine, human.participant_id, {"message": "hello"})
    turn = build_page_model(match).turns[0]
    assert turn.message_html is not None
    assert turn.orders_html is None


def test_non_mapping_action_renders_as_orders_block() -> None:
    match, engine = start_match()
    human, _agent = match.participants
    match.take_turn(engine, human.participant_id, 42)
    turn = build_page_model(match).turns[0]
    assert turn.message_html is None
    assert turn.orders_html is not None
    assert "42" in turn.orders_html


def test_no_turns_yet_renders_a_placeholder() -> None:
    match, _ = start_match()
    model = build_page_model(match)
    html_body = render_page_body(model)
    assert "No turns yet." in html_body


def test_turns_are_rendered_in_order_with_correct_turn_numbers() -> None:
    match, engine = start_match()
    human, agent = match.participants
    for i in range(5):
        actor = human if i % 2 == 0 else agent
        match.take_turn(engine, actor.participant_id, {"delta": 1})

    model = build_page_model(match)
    assert [t.turn_number for t in model.turns] == [1, 2, 3, 4, 5]
    assert [t.participant_id for t in model.turns] == [
        human.participant_id,
        agent.participant_id,
        human.participant_id,
        agent.participant_id,
        human.participant_id,
    ]

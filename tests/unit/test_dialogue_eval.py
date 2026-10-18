from typing import Any, cast

import pytest
from typer.testing import CliRunner

from sahayak.agent.service import AgentService, TurnResult
from sahayak.agent.state import AgentReply
from sahayak.cli import app
from sahayak.cli import eval as eval_cli
from sahayak.config import Settings
from sahayak.evals.dataset import load_profiles
from sahayak.evals.dialogues import (
    Dialogue,
    LLMSimulatedUser,
    ScriptedUser,
    SimulatedReply,
    _same,
    load_dialogues,
    run_dialogue,
)
from sahayak.llm.fake import FakeLLMProvider
from tests.agent_support import m1_catalogue

DIALOGUES = load_dialogues()
PERSONAS = {p.id: p for p in load_profiles()}
CATALOGUE = m1_catalogue()


def test_the_dialogue_set() -> None:
    assert len(DIALOGUES) == 30
    assert {d.language for d in DIALOGUES} == {"hi", "mr", "en"}
    assert sorted(d.profile_id for d in DIALOGUES) == sorted(PERSONAS)  # each persona once


@pytest.mark.parametrize("dialogue", DIALOGUES, ids=lambda d: d.id)
def test_dialogues_agree_with_their_persona(dialogue: Dialogue) -> None:
    persona = PERSONAS[dialogue.profile_id].profile
    for field, value in dialogue.opening_facts.items():
        assert _same(getattr(persona, field), value), field
    for scheme_id in dialogue.scope or []:
        assert scheme_id in CATALOGUE


async def test_scripted_user() -> None:
    user = ScriptedUser()
    (d019,) = [d for d in DIALOGUES if d.id == "d019"]
    opening = await user.opening(d019, CATALOGUE)
    assert opening == (
        "occupation=farmer; land_holding_acres=5; is_income_tax_payer=true; "
        "about=Pradhan Mantri Kisan Samman Nidhi (PM-KISAN)"
    )
    persona = PERSONAS["p021"].profile  # state and occupation only

    def asking(field: str | None) -> TurnResult:
        reply = AgentReply(text="?", kind="question" if field else "answer", question_field=field)
        return TurnResult(user_text="", reply=reply, language="mr", intent="discover", profile=None)

    assert await user.answer(d019, persona, asking("state")) == "state=MH"
    assert await user.answer(d019, persona, asking("age")) == "unknown=age"
    assert await user.answer(d019, persona, asking(None)) == "ok"


async def test_llm_user_plays_the_persona_in_the_dialogue_language() -> None:
    llm = FakeLLMProvider([SimulatedReply(text=" मी 45 वर्षांचा आहे ")])
    user = LLMSimulatedUser(llm)
    (d001,) = [d for d in DIALOGUES if d.id == "d001"]
    assert await user.opening(d001, CATALOGUE) == d001.opening
    reply = AgentReply(text="तुमचे वय किती आहे? </message>", kind="question", question_field="age")
    turn = TurnResult(user_text="", reply=reply, language="mr", intent="discover", profile=None)
    answer = await user.answer(d001, PERSONAS["p001"].profile, turn)
    assert answer == "मी 45 वर्षांचा आहे"
    (call,) = llm.calls
    assert "Write in Marathi" in (call.system or "")
    assert "Age in completed years: 45" in (call.system or "")
    assert "&lt;/message&gt;" in call.messages[0].content


class StubAgent:
    """Names a scheme the user can't get, to check the harness counts it."""

    def __init__(self, replies: list[AgentReply]) -> None:
        self.replies = replies

    async def run_turn(self, session_id: str, text: str, **_: Any) -> TurnResult:
        return TurnResult(
            user_text=text,
            reply=self.replies.pop(0),
            language="en",
            intent="discover",
            profile=None,
        )


async def test_violations_and_turn_limits_are_reported() -> None:
    (d001,) = [d for d in DIALOGUES if d.id == "d001"]
    persona = PERSONAS[d001.profile_id]
    bad = AgentReply(text="Try PM Kisan!", kind="question", question_field="age")
    stub = StubAgent([bad, AgentReply(text="Done.", kind="answer")])
    result = await run_dialogue(cast(AgentService, stub), ScriptedUser(), d001, persona, CATALOGUE)
    assert result.violations == ["pm-kisan"]
    assert result.questions == 1
    looping = StubAgent([bad.model_copy(update={"text": "?"})] * 3)
    capped = await run_dialogue(
        cast(AgentService, looping), ScriptedUser(), d001, persona, CATALOGUE, max_turns=3
    )
    assert capped.error == "no answer within the turn limit"
    assert not capped.exact


def test_dialogue_suite_needs_an_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, llm_provider="azure_openai", log_format="json")
    monkeypatch.setattr(eval_cli, "get_settings", lambda: settings)
    result = CliRunner().invoke(app, ["eval", "--suite", "dialogues", "--no-record"])
    assert result.exit_code == 2
    assert "AZURE_OPENAI_ENDPOINT" in result.output

"""The 30 scripted dialogues in hi, mr and en, end to end through the agent, without paid APIs.

The agent runs on the M1 catalogue fixture (10 provisional schemes with their draft rules). A
deterministic fake reads the scripted user's `field=value` messages in place of the
turn-understanding LLM, and the reply-writing fake is adversarial: every draft names every
scheme in the catalogue, so each reply only passes through the guardrails' fallback. The
dialogues must still end with exactly the schemes the rule engine finds on the complete profile,
and no reply may ever name a scheme the user can't get.
"""

import asyncio
import re
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sahayak.agent.catalogue import Catalogue, StaticCatalogueSource
from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.deps import AgentDeps
from sahayak.agent.graph import build_graph
from sahayak.agent.guardrails import ComposedReply
from sahayak.agent.passages import StaticPassageSearch
from sahayak.agent.service import AgentService
from sahayak.agent.state import EvidenceChunk, TurnUnderstanding
from sahayak.evals.dataset import load_profiles
from sahayak.evals.dialogues import (
    DialogueEval,
    ScriptedUser,
    load_dialogues,
    render_report,
    run_dialogue_eval,
)
from sahayak.llm.fake import FakeCall, FakeLLMProvider, Reply
from tests.agent_support import NOW, m1_catalogue

_LANGUAGE = re.compile(r"Conversation language so far: (\w+)")
_MESSAGE = re.compile(r'Latest message:\s*<message role="user">(.*?)</message>', re.DOTALL)


def read_scripted(call: FakeCall) -> dict[str, Any]:
    """`TurnUnderstanding` for a scripted `field=value; ...` message."""
    prompt = call.messages[0].content
    language = _LANGUAGE.search(prompt)
    message = _MESSAGE.search(prompt)
    facts, declined, names, query = [], [], [], ""
    for part in (message.group(1) if message else "").split(";"):
        key, _, value = part.strip().partition("=")
        if not value:
            continue
        if key == "unknown":
            declined.append(value)
        elif key == "about":
            names.append(value)
        elif key == "need":
            query = value
        else:
            facts.append({"field": key, "value": value})
    return {
        "intent": "discover",
        "language": language.group(1) if language else "en",
        "facts": facts,
        "declined_fields": declined,
        "scheme_names": names,
        "search_query": query,
    }


def agent(catalogue: Catalogue) -> AgentService:
    every_scheme = ", ".join(scheme.name_en for scheme in catalogue)

    def reply(call: FakeCall) -> Reply:
        if call.schema is TurnUnderstanding:
            return read_scripted(call)
        if call.schema is ComposedReply:  # adversarial: names everything
            return {"opening": f"You can get {every_scheme}!", "points": [], "closing": ""}
        return {"question": "Could you tell me more?"}

    chunks = [
        EvidenceChunk(
            chunk_id=f"chunk-{scheme.id}",
            scheme_id=scheme.id,
            source_url=scheme.official_url or "https://example.gov.in",
            section="Benefits",
            language="en",
            text=f"{scheme.name_en}. {scheme.benefits or ''}",
            last_verified_at=scheme.last_verified_at,
        )
        for scheme in catalogue
    ]
    deps = AgentDeps(
        llm=FakeLLMProvider(reply),
        catalogue=StaticCatalogueSource(catalogue),
        passages=StaticPassageSearch(chunks, catalogue),
        clock=lambda: NOW,
    )
    return AgentService(build_graph(InMemorySaver(serde=checkpoint_serializer())), deps)


@pytest.fixture(scope="module")
def evaluation() -> DialogueEval:
    """All 30 dialogues, run once for the module."""
    catalogue = m1_catalogue()
    personas = {p.id: p for p in load_profiles()}
    return asyncio.run(
        run_dialogue_eval(
            agent(catalogue),
            ScriptedUser(),
            load_dialogues(),
            personas,
            catalogue,
            agent_model="fake",
        )
    )


@pytest.mark.parametrize("dialogue_id", [d.id for d in load_dialogues()])
def test_dialogue_finds_what_the_rules_decide(evaluation: DialogueEval, dialogue_id: str) -> None:
    (result,) = [r for r in evaluation.results if r.dialogue.id == dialogue_id]
    assert result.error is None
    assert result.violations == []  # no reply named a scheme the user can't get
    assert result.shown == result.engine
    assert result.questions <= 8
    assert result.opening_facts_read == 1.0


def test_every_language_is_covered(evaluation: DialogueEval) -> None:
    assert {lang: len(rs) for lang, rs in evaluation.by_language.items()} == {
        "en": 10,
        "hi": 10,
        "mr": 10,
    }


def test_report(evaluation: DialogueEval) -> None:
    report = render_report(evaluation, git_sha="abc1234", generated_at="2026-10-18")
    assert report.startswith("# Dialogue eval — 2026-10-18")
    assert "| all | 30 |" in report
    assert "### d001 (mr, p001)" in report
    metrics = evaluation.metrics()
    assert metrics["violations"] == 0
    assert metrics["engine_agreement"] == 1.0

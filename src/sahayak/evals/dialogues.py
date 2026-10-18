"""End-to-end conversation eval: a simulated user talks to the agent (TRD §12.2, M4-T13).

Each dialogue in `evals/dialogues.jsonl` pairs a persona from the eligibility eval set
(`evals/profiles.jsonl`, hand-checked labels) with a conversation language and an opening
message. The simulated user answers the agent's questions from the persona's profile, and says
it doesn't know what the profile leaves out. Two simulated users:

- `ScriptedUser` writes facts as `field=value` text. Deterministic: with a fake agent LLM that
  reads that format, the whole eval runs in CI without any paid API.
- `LLMSimulatedUser` plays the persona in natural Hindi, Marathi or English (prompt
  `simulated_user`), for live runs against the real agent.

Scores per dialogue: the schemes finally shown against the reference labels (exact set and per
scheme), against the rule engine on the full profile (did the conversation find what the rules
can decide?), questions asked, turns, and every reply that named a scheme it shouldn't have
(must be none).
"""

import statistics
import uuid
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.agent.catalogue import Catalogue
from sahayak.agent.nodes.check_eligibility import effective_rule
from sahayak.agent.replies import LANGUAGE_NAMES
from sahayak.agent.service import AgentService, TurnResult
from sahayak.agent.untrusted import as_data
from sahayak.db.models import EvalRun
from sahayak.eligibility.engine import Verdict, evaluate
from sahayak.eligibility.fields import PROFILE_FIELDS, ProfileFieldName
from sahayak.eligibility.profile import Language, UserProfile
from sahayak.evals.dataset import EvalProfile
from sahayak.llm.base import ChatMessage, LLMProvider
from sahayak.llm.prompts import load_prompt

DIALOGUES_PATH = Path("evals/dialogues.jsonl")
MAX_TURNS = 12
SIMULATED_USER_PROMPT = "simulated_user"

FactValue = bool | int | float | str


class Dialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^d\d{3}$")
    profile_id: str = Field(pattern=r"^p\d{3}$")
    language: Language
    opening: str = Field(min_length=1)
    # What the opening states, as profile values (for the scripted user, and to check reading).
    opening_facts: dict[ProfileFieldName, FactValue]
    need: str | None = None  # the topic the opening asks about, in English, if any
    # Schemes the conversation is about (a named scheme or a topic); None means all of them.
    scope: list[str] | None = None


def load_dialogues(path: Path = DIALOGUES_PATH) -> list[Dialogue]:
    dialogues = [
        Dialogue.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [d.id for d in dialogues]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate dialogue ids")
    return dialogues


class SimulatedUser(ABC):
    @abstractmethod
    async def opening(self, dialogue: Dialogue, catalogue: Catalogue) -> str: ...

    @abstractmethod
    async def answer(self, dialogue: Dialogue, persona: UserProfile, reply: TurnResult) -> str: ...


def format_fact(value: FactValue) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


class ScriptedUser(SimulatedUser):
    """States facts as `field=value; ...`, names schemes as `about=<name>`, the need as
    `need=<topic>`, and a field it doesn't know as `unknown=<field>`."""

    async def opening(self, dialogue: Dialogue, catalogue: Catalogue) -> str:
        parts = [f"{k}={format_fact(v)}" for k, v in dialogue.opening_facts.items()]
        for scheme_id in catalogue.mentions(dialogue.opening):
            scheme = catalogue.get(scheme_id)
            if scheme is not None:
                parts.append(f"about={scheme.name_en}")
        if dialogue.need:
            parts.append(f"need={dialogue.need}")
        return "; ".join(parts) or "which schemes can I get?"

    async def answer(self, dialogue: Dialogue, persona: UserProfile, reply: TurnResult) -> str:
        asked = reply.reply.question_field
        if asked is None:
            return "ok"
        value = getattr(persona, asked, None)
        return f"unknown={asked}" if value is None else f"{asked}={format_fact(value)}"


class SimulatedReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class LLMSimulatedUser(SimulatedUser):
    """Plays the persona in the dialogue's language with the configured LLM."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def opening(self, dialogue: Dialogue, catalogue: Catalogue) -> str:
        return dialogue.opening

    async def answer(self, dialogue: Dialogue, persona: UserProfile, reply: TurnResult) -> str:
        known = persona.model_dump(exclude_none=True, exclude={"language", "field_sources"})
        prompt = load_prompt(SIMULATED_USER_PROMPT).render(
            language_name=LANGUAGE_NAMES[dialogue.language],
            facts=[
                {"name": name, "description": PROFILE_FIELDS[name].description, "value": value}
                for name, value in known.items()
            ],
            message=as_data(reply.reply.text),
        )
        response = await self._llm.structured(
            [ChatMessage("user", prompt.user)], SimulatedReply, system=prompt.system, max_tokens=300
        )
        return response.value.text.strip() or "?"


def engine_eligible(catalogue: Catalogue, profile: UserProfile) -> set[str]:
    """Schemes the rules make ELIGIBLE (and displayable) on the complete profile."""
    eligible = set()
    for scheme in catalogue.evaluable(profile.state):
        rule = effective_rule(scheme)
        if rule is None:
            continue
        try:
            if evaluate(rule, profile).verdict is Verdict.ELIGIBLE:
                eligible.add(scheme.id)
        except (ValueError, AttributeError):
            continue
    return eligible


@dataclass
class Turn:
    user: str
    reply: str
    kind: str
    question_field: str | None
    shown: list[str]
    violations: list[str]
    latency_ms: int


@dataclass
class DialogueResult:
    dialogue: Dialogue
    turns: list[Turn]
    shown: set[str]  # schemes shown at the end, within the dialogue's scope
    reference: set[str]  # labelled ELIGIBLE, within scope
    engine: set[str]  # rule engine ELIGIBLE on the full profile, within scope
    scope: set[str]
    opening_facts_read: float  # share of the opening's facts the agent saved after turn 1
    error: str | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0

    @property
    def questions(self) -> int:
        return sum(turn.kind == "question" for turn in self.turns)

    @property
    def violations(self) -> list[str]:
        return [v for turn in self.turns for v in turn.violations]

    @property
    def exact(self) -> bool:
        return self.error is None and self.shown == self.reference

    @property
    def agrees_with_engine(self) -> bool:
        return self.error is None and self.shown == self.engine

    def scheme_hits(self) -> tuple[int, int]:
        """(correct, total) per-scheme decisions against the reference labels."""
        correct = sum((s in self.shown) == (s in self.reference) for s in self.scope)
        return correct, len(self.scope)


def _shown(result: TurnResult) -> list[str]:
    return [s.scheme_id for s in (*result.ready, *result.action_needed, *result.provisional)]


async def run_dialogue(
    service: AgentService,
    user: SimulatedUser,
    dialogue: Dialogue,
    persona: EvalProfile,
    catalogue: Catalogue,
    *,
    max_turns: int = MAX_TURNS,
) -> DialogueResult:
    session_id = f"eval-{dialogue.id}-{uuid.uuid4().hex[:8]}"
    scope = set(dialogue.scope) if dialogue.scope is not None else set(catalogue.schemes)
    reference = {s for s, v in persona.expected.items() if v is Verdict.ELIGIBLE} & scope
    engine = engine_eligible(catalogue, persona.profile) & scope
    named: set[str] = set()
    turns: list[Turn] = []
    result: TurnResult | None = None
    facts_read = 0.0
    error = None
    usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
    cost: float | None = 0.0
    text = await user.opening(dialogue, catalogue)
    for number in range(1, max_turns + 1):
        named |= set(catalogue.mentions(text))
        result = await service.run_turn(
            session_id, text, language=dialogue.language, consents=["profile", "sensitive"]
        )
        shown_now = _shown(result)
        mentioned = catalogue.mentions(result.reply.text)
        turns.append(
            Turn(
                user=text,
                reply=result.reply.text,
                kind=result.reply.kind,
                question_field=result.reply.question_field,
                shown=shown_now,
                violations=[s for s in mentioned if s not in set(shown_now) | named],
                latency_ms=result.latency_ms,
            )
        )
        usage["llm_calls"] += result.usage.calls
        usage["input_tokens"] += result.usage.input_tokens
        usage["output_tokens"] += result.usage.output_tokens
        cost = (
            None if cost is None or result.usage.cost_usd is None else cost + result.usage.cost_usd
        )
        if number == 1 and result.profile is not None and dialogue.opening_facts:
            read = sum(
                _same(getattr(result.profile, k), v) for k, v in dialogue.opening_facts.items()
            )
            facts_read = read / len(dialogue.opening_facts)
        if result.reply.kind == "error":
            error = "agent error"
            break
        if result.reply.kind != "question":
            break
        text = await user.answer(dialogue, persona.profile, result)
    else:
        error = "no answer within the turn limit"
    return DialogueResult(
        dialogue=dialogue,
        turns=turns,
        shown=set(_shown(result)) & scope if result else set(),
        reference=reference,
        engine=engine,
        scope=scope,
        opening_facts_read=facts_read,
        error=error,
        cost_usd=cost,
        **usage,
    )


def _same(actual: Any, expected: FactValue) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return float(actual) == float(expected)
    return bool(actual == expected)


@dataclass
class DialogueEval:
    results: list[DialogueResult]
    user: str
    agent_model: str
    by_language: dict[str, list[DialogueResult]] = field(init=False)

    def __post_init__(self) -> None:
        self.by_language = {}
        for result in self.results:
            self.by_language.setdefault(result.dialogue.language, []).append(result)

    @staticmethod
    def summary(results: Sequence[DialogueResult]) -> dict[str, Any]:
        if not results:
            return {}
        hits = [r.scheme_hits() for r in results]
        latencies = [t.latency_ms for r in results for t in r.turns]
        shown = sum(len(r.shown) for r in results)
        wrongly_shown = sum(len(r.shown - r.reference) for r in results)
        eligible = sum(len(r.reference) for r in results)
        found = sum(len(r.shown & r.reference) for r in results)
        costs = [r.cost_usd for r in results]
        return {
            "dialogues": len(results),
            "exact_accuracy": sum(r.exact for r in results) / len(results),
            "scheme_accuracy": sum(c for c, _ in hits) / max(1, sum(t for _, t in hits)),
            "engine_agreement": sum(r.agrees_with_engine for r in results) / len(results),
            "shown_precision": None if shown == 0 else (shown - wrongly_shown) / shown,
            "eligible_recall": None if eligible == 0 else found / eligible,
            "violations": sum(len(r.violations) for r in results),
            "errors": sum(r.error is not None for r in results),
            "questions_mean": statistics.mean(r.questions for r in results),
            "questions_max": max(r.questions for r in results),
            "turns_mean": statistics.mean(len(r.turns) for r in results),
            "opening_facts_read": statistics.mean(r.opening_facts_read for r in results),
            "turn_latency_p50_ms": statistics.median(latencies) if latencies else None,
            "turn_latency_max_ms": max(latencies) if latencies else None,
            "llm_calls": sum(r.llm_calls for r in results),
            "input_tokens": sum(r.input_tokens for r in results),
            "output_tokens": sum(r.output_tokens for r in results),
            "cost_usd": None if None in costs else round(sum(c or 0.0 for c in costs), 4),
        }

    def metrics(self) -> dict[str, Any]:
        return {
            **self.summary(self.results),
            "by_language": {
                lang: self.summary(rs) for lang, rs in sorted(self.by_language.items())
            },
        }


async def run_dialogue_eval(
    service: AgentService,
    user: SimulatedUser,
    dialogues: Sequence[Dialogue],
    personas: dict[str, EvalProfile],
    catalogue: Catalogue,
    *,
    agent_model: str,
    on_result: Callable[[DialogueResult], None] | None = None,
) -> DialogueEval:
    results = []
    for dialogue in dialogues:
        result = await run_dialogue(
            service, user, dialogue, personas[dialogue.profile_id], catalogue
        )
        results.append(result)
        if on_result:
            on_result(result)
    return DialogueEval(results, user=type(user).__name__, agent_model=agent_model)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def render_report(evaluation: DialogueEval, *, git_sha: str | None, generated_at: str) -> str:
    metrics = evaluation.metrics()
    lines = [
        f"# Dialogue eval — {generated_at}",
        "",
        f"Simulated user: `{evaluation.user}`. Agent model: `{evaluation.agent_model}`. "
        f"Commit: `{git_sha or 'unknown'}`.",
        "",
        "Reference labels are the hand-checked eligibility labels (`evals/profiles.jsonl`). "
        '"Engine" is the rule engine on each persona\'s complete profile: agreement with it '
        "shows whether the conversation found everything the current rules can decide.",
        "",
        "| Language | Dialogues | Exact (reference) | Per scheme (reference) | Engine agreement "
        "| Shown precision | Recall | Violations | Questions (mean / max) | Turns | "
        "Opening facts read | p50 turn |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [("all", metrics), *metrics["by_language"].items()]
    for name, m in rows:
        if not m:
            continue
        p50 = m["turn_latency_p50_ms"]
        lines.append(
            f"| {name} | {m['dialogues']} | {_pct(m['exact_accuracy'])} | "
            f"{_pct(m['scheme_accuracy'])} | {_pct(m['engine_agreement'])} | "
            f"{_pct(m['shown_precision'])} | {_pct(m['eligible_recall'])} | {m['violations']} | "
            f"{m['questions_mean']:.1f} / {m['questions_max']} | {m['turns_mean']:.1f} | "
            f"{_pct(m['opening_facts_read'])} | {'n/a' if p50 is None else f'{p50:.0f} ms'} |"
        )
    cost = metrics["cost_usd"]
    lines += [
        "",
        f"LLM: {metrics['llm_calls']} calls, {metrics['input_tokens']} input and "
        f"{metrics['output_tokens']} output tokens, cost "
        f"{'unknown' if cost is None else f'${cost:.2f}'}. Errors: {metrics['errors']}.",
        "",
        "## Dialogues",
        "",
        "| Dialogue | Persona | Lang | Shown | Reference | Engine | Questions | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in evaluation.results:
        notes = []
        if r.error:
            notes.append(r.error)
        if r.violations:
            notes.append(f"named outside the list: {', '.join(r.violations)}")
        if not r.exact and r.error is None:
            missed, extra = r.reference - r.shown, r.shown - r.reference
            notes += [f"missed {', '.join(sorted(missed))}"] if missed else []
            notes += [f"extra {', '.join(sorted(extra))}"] if extra else []
        lines.append(
            f"| {r.dialogue.id} | {r.dialogue.profile_id} | {r.dialogue.language} | "
            f"{', '.join(sorted(r.shown)) or '-'} | {', '.join(sorted(r.reference)) or '-'} | "
            f"{', '.join(sorted(r.engine)) or '-'} | {r.questions} | "
            f"{_cell('; '.join(notes)) or 'ok'} |"
        )
    lines += ["", "## Transcripts", ""]
    for r in evaluation.results:
        lines.append(f"### {r.dialogue.id} ({r.dialogue.language}, {r.dialogue.profile_id})")
        lines.append("")
        for number, turn in enumerate(r.turns, start=1):
            lines.append(f"{number}. **User:** {_cell(turn.user)}")
            lines.append(f"   **Sahayak** ({turn.kind}): {_cell(turn.reply)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def question_counts(evaluation: DialogueEval) -> Counter[str]:
    """How often each profile field was asked across the eval (for tuning the planner)."""
    return Counter(
        turn.question_field
        for result in evaluation.results
        for turn in result.turns
        if turn.question_field
    )


def run_config(evaluation: DialogueEval, *, dataset: Path) -> dict[str, Any]:
    return {
        "dataset": str(dataset),
        "dialogues": len(evaluation.results),
        "simulated_user": evaluation.user,
        "agent_model": evaluation.agent_model,
    }


async def record_run(
    session: AsyncSession, evaluation: DialogueEval, *, git_sha: str | None, config: dict[str, Any]
) -> EvalRun:
    metrics = evaluation.metrics()
    metrics["questions_by_field"] = dict(question_counts(evaluation))
    run = EvalRun(suite="dialogues", git_sha=git_sha, config=config, metrics=metrics)
    session.add(run)
    await session.flush()
    return run

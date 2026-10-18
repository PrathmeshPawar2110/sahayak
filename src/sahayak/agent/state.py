"""The agent's state (TRD §10), saved after every turn by the LangGraph checkpointer.

`AgentState` is the TRD's state plus a few fields the nodes need to carry between turns (questions
asked, declined fields, contradictions to confirm, the current need and focus) and some that only
live for one turn (the LLM's reading of the message, the reply). `load_user_context` resets the
per-turn fields at the start of every turn.

Values are plain data or the Pydantic models below. Every model stored directly in the state is
listed in `sahayak.agent.checkpoint.STATE_TYPES`, the checkpointer's deserialisation allowlist.
"""

from datetime import date, datetime
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field

from sahayak.eligibility.engine import EligibilityResult
from sahayak.eligibility.fields import ProfileFieldName
from sahayak.eligibility.profile import Language, UserProfile

Intent = Literal[
    "discover",
    "ask_scheme_info",
    "select_scheme",
    "get_form",
    "upload_document",
    "my_documents",
    "manage_reminders",
    "reset",
    "delete_account",
    "smalltalk",
    "out_of_scope",
]

# Consent purposes an anonymous session can grant (TRD §5 Privacy, items a and c). The vault and
# reminder purposes need an account (M10, M11).
ConsentPurpose = Literal["profile", "sensitive"]

# A profile value as the agent handles it.
FieldValue = bool | int | float | str


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExtractedFact(_Model):
    """One profile value the user stated, as the LLM read it (canonical text, see the prompt)."""

    field: ProfileFieldName
    value: str


class TurnUnderstanding(_Model):
    """The LLM's structured reading of the latest user message (prompt `turn_understanding`).

    The schema has no defaults: strict structured-output modes need every key present.
    """

    intent: Intent
    language: Language = Field(description="Language of the user's latest message.")
    facts: list[ExtractedFact]
    declined_fields: list[ProfileFieldName]
    scheme_names: list[str]
    search_query: str


class PendingQuestion(_Model):
    """What the agent asked at the end of the previous turn."""

    field: str
    kind: Literal["ask", "confirm"]  # ask for a missing value, or confirm a contradiction


class Contradiction(_Model):
    """A new answer that disagrees with a known value (FR-14). The known value stays until the
    user confirms which one is right."""

    field: str
    current: FieldValue
    new: FieldValue
    asked: bool = False


class EvidenceChunk(_Model):
    """A retrieved scheme passage the reply may cite."""

    chunk_id: str
    scheme_id: str
    source_url: str
    section: str
    language: Language
    text: str
    last_verified_at: datetime | None = None


class DisplayItem(_Model):
    """A scheme the user may see, with the rule conditions that made it eligible."""

    scheme_id: str
    rule_version: int
    provisional: bool = False
    reasons: list[str] = Field(default_factory=list)


class DisplayResults(_Model):
    """Output of `filter_results` (TRD §8C): the only schemes a reply may recommend."""

    ready: list[DisplayItem] = Field(default_factory=list)
    action_needed: list[DisplayItem] = Field(default_factory=list)  # used from M10
    provisional: list[DisplayItem] = Field(default_factory=list)

    def items(self) -> list[DisplayItem]:
        return [*self.ready, *self.action_needed, *self.provisional]

    def scheme_ids(self) -> list[str]:
        return [item.scheme_id for item in self.items()]


class DocumentSummary(_Model):
    """A saved document as the agent sees it, without its content (filled from M10)."""

    type: str
    status: str
    expires_at: date | None = None


class Citation(_Model):
    number: int
    chunk_id: str
    scheme_id: str
    section: str
    source_url: str
    last_verified_at: datetime | None = None


ReplyKind = Literal["question", "answer", "notice", "reset", "error"]
GuardOutcome = Literal["passed", "regenerated", "template"]


class AgentReply(_Model):
    """What the agent says at the end of a turn."""

    text: str
    kind: ReplyKind
    citations: list[Citation] = Field(default_factory=list)
    question_field: str | None = None
    guard: GuardOutcome | None = None  # how the reply passed the post-check (compose_response)


class AgentState(TypedDict, total=False):
    # --- TRD §10
    session_id: str
    language: Language
    messages: Annotated[list[BaseMessage], add_messages]
    profile: UserProfile
    candidate_schemes: list[str]
    eligibility: dict[str, EligibilityResult]
    pending_question: PendingQuestion | None
    selected_scheme: str | None
    retrieved_context: list[EvidenceChunk]
    turn_count: int
    user_id: str | None
    member_id: str | None
    documents: list[DocumentSummary]
    display_results: DisplayResults
    intent: Intent
    # --- kept across turns
    consents: list[ConsentPurpose]  # given with every turn by the API
    questions_asked: int
    declined_fields: list[str]
    contradictions: list[Contradiction]
    need: str | None  # what the user wants help with, as English search text
    focus_schemes: list[str]  # schemes the user asked about by name
    # --- this turn only
    understanding: TurnUnderstanding | None
    named_schemes: list[str]
    pii_found: list[str]  # kinds of PII masked out of the user's message
    reply: AgentReply | None

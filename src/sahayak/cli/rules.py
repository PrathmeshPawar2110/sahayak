"""`sahayak rules ...`: list rule status per scheme and review LLM-drafted rules."""

import asyncio
from collections.abc import Callable
from typing import Annotated

import click
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.config import get_settings
from sahayak.db.models import EligibilityRule, RuleStatus, Scheme
from sahayak.db.session import create_engine, create_session_factory
from sahayak.eligibility.rules import describe, parse_logic
from sahayak.ingestion.review import (
    ReviewError,
    apply_edit,
    approve_rule,
    approved_rule,
    current_issues,
    editable_json,
    excerpts_of,
    latest_draft,
    reject_rule,
)
from sahayak.observability.logging import configure_logging

Ask = Callable[[str], str]
Edit = Callable[[str], str | None]

rules_app = typer.Typer(help="Review LLM-drafted eligibility rules.", no_args_is_help=True)
console = Console()


@rules_app.command("list")
def list_rules() -> None:
    """Show each scheme's live (approved) rule and pending draft."""
    settings = get_settings()
    configure_logging(settings)

    async def run() -> None:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as session:
                console.print(await rules_table(session))
        finally:
            await engine.dispose()

    asyncio.run(run())


@rules_app.command("review")
def review(
    scheme_id: Annotated[str, typer.Argument(help="Scheme id, as in the manifest.")],
    reviewer: Annotated[
        str, typer.Option(envvar="SAHAYAK_REVIEWER", prompt="Reviewer name", help="Your name.")
    ],
) -> None:
    """Show the latest draft beside its source text, then approve, edit or reject it."""
    settings = get_settings()
    configure_logging(settings)

    async def run() -> str:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as session:
                return await review_rule(
                    session,
                    scheme_id,
                    reviewer=reviewer,
                    console=console,
                    ask=lambda prompt: str(typer.prompt(prompt)),
                    edit=lambda text: click.edit(text, extension=".json"),
                )
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    except ReviewError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


async def rules_table(session: AsyncSession) -> Table:
    schemes = (await session.scalars(select(Scheme).order_by(Scheme.id))).all()
    rules = (
        await session.scalars(
            select(EligibilityRule).where(
                EligibilityRule.status.in_([RuleStatus.APPROVED, RuleStatus.DRAFT])
            )
        )
    ).all()
    approved = {r.scheme_id: r for r in rules if r.status is RuleStatus.APPROVED}
    drafts: dict[str, EligibilityRule] = {}
    for rule in rules:
        if rule.status is RuleStatus.DRAFT and rule.version > getattr(
            drafts.get(rule.scheme_id), "version", 0
        ):
            drafts[rule.scheme_id] = rule

    table = Table(title="Eligibility rules")
    for column in ("Scheme", "Status", "Approved", "Draft", "Confidence", "Issues"):
        table.add_column(column)
    for scheme in schemes:
        live, draft = approved.get(scheme.id), drafts.get(scheme.id)
        extraction = (draft.extraction or {}) if draft else {}
        confidence = extraction.get("confidence")
        issues = len(current_issues(draft)) if draft else None
        table.add_row(
            scheme.id,
            scheme.status.value,
            f"v{live.version}" if live else "-",
            f"v{draft.version}" if draft else "-",
            f"{confidence:.2f}" if isinstance(confidence, int | float) else "-",
            "-" if issues is None else (f"[red]{issues}[/red]" if issues else "0"),
        )
    return table


async def review_rule(
    session: AsyncSession,
    scheme_id: str,
    *,
    reviewer: str,
    console: Console,
    ask: Ask,
    edit: Edit,
) -> str:
    """Interactive review loop. Returns approved / rejected / quit / no draft."""
    scheme = await session.get(Scheme, scheme_id)
    if scheme is None:
        raise ReviewError(f"unknown scheme {scheme_id!r}")
    rule = await latest_draft(session, scheme_id)
    if rule is None:
        console.print(f"No draft rule to review for {scheme_id}.")
        return "no draft"
    live = await approved_rule(session, scheme_id)

    while True:
        show_rule(console, scheme, rule, live)
        choice = ask("[a]pprove, [e]dit, [r]eject or [q]uit").strip().lower()[:1]
        if choice == "a":
            try:
                await approve_rule(session, rule, reviewer=reviewer)
            except ReviewError as exc:
                console.print(f"[red]{exc}[/red]")
                continue
            await session.commit()
            console.print(f"[green]Approved v{rule.version} of {scheme_id}.[/green]")
            return "approved"
        if choice == "e":
            edited = edit(editable_json(rule))
            if edited is None:
                console.print("No changes saved.")
                continue
            try:
                apply_edit(rule, edited)
            except ReviewError as exc:
                console.print(f"[red]{exc}[/red]")
                continue
            await session.commit()  # keep the edit even if the reviewer quits next
            continue
        if choice == "r":
            await reject_rule(session, rule, reviewer=reviewer, reason=ask("Reason"))
            await session.commit()
            console.print(f"Rejected v{rule.version} of {scheme_id}.")
            return "rejected"
        if choice == "q":
            return "quit"
        console.print("Please answer a, e, r or q.")


def show_rule(
    console: Console, scheme: Scheme, rule: EligibilityRule, live: EligibilityRule | None
) -> None:
    extraction = rule.extraction or {}
    header = Text()
    header.append(f"{scheme.name_en} ({scheme.id})", style="bold")
    header.append(f"   scheme status: {scheme.status.value}\n")
    header.append(f"Draft v{rule.version}")
    if rule.review_edited:
        header.append(" (edited)", style="yellow")
    header.append(
        f"   live rule: {f'v{live.version}' if live else 'none'}"
        f"   model: {extraction.get('model', '?')}   prompt: {extraction.get('prompt', '?')}"
        f"   confidence: {extraction.get('confidence', '?')}"
    )
    console.print(Panel(header))

    table = Table(expand=True)
    table.add_column("Source text (what the model read)", ratio=1)
    table.add_column("Draft rule", ratio=1)
    table.add_row(_source_text(rule), _rule_text(rule))
    console.print(table)


def _source_text(rule: EligibilityRule) -> Text:
    text = Text()
    excerpts = excerpts_of(rule)
    if not excerpts:
        return Text("(no excerpts recorded)", style="dim")
    for excerpt in excerpts:
        text.append(f"{excerpt.section}\n", style="bold")
        text.append(f"{excerpt.url}\n", style="dim")
        text.append(f"{excerpt.text}\n\n")
    if (rule.extraction or {}).get("truncated"):
        text.append("(Only eligibility-related sections were sent to the model.)", style="dim")
    return text


def _rule_text(rule: EligibilityRule) -> Text:
    text = Text()
    try:
        text.append(describe(parse_logic(rule.logic)) + "\n")
    except ValidationError:
        text.append(f"{rule.logic}\n", style="red")
    extraction = rule.extraction or {}
    unmapped = extraction.get("unmapped_criteria") or []
    if unmapped:
        text.append("\nNot expressible as conditions:\n", style="bold yellow")
        for criterion in unmapped:
            text.append(f"- {criterion}\n")
    if rule.notes:
        text.append("\nNotes: ", style="bold")
        text.append(f"{rule.notes}\n")
    if rule.source_refs:
        text.append("\nSources:\n", style="bold")
        for ref in rule.source_refs:
            text.append(f"- {ref.get('section')} ({ref.get('url')})\n")
    issues = current_issues(rule)
    if issues:
        text.append("\nIssues (must be fixed before approval):\n", style="bold red")
        for issue in issues:
            text.append(f"- {issue}\n", style="red")
    return text

"""The `sahayak` admin command line (TRD §8). Run `sahayak --help` or `python -m sahayak.cli`."""

import typer

from sahayak.cli.eval import eval_command
from sahayak.cli.ingest import ingest_command
from sahayak.cli.latency import latency_command
from sahayak.cli.rules import rules_app

app = typer.Typer(
    name="sahayak", help="Sahayak admin commands.", no_args_is_help=True, add_completion=False
)
app.command("ingest")(ingest_command)
app.command("eval")(eval_command)
app.command("latency")(latency_command)
app.add_typer(rules_app, name="rules")

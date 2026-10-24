"""`sahayak openapi`: dump the API's OpenAPI schema, for `web/`'s generated client (M6-T01)."""

import json
from pathlib import Path
from typing import Annotated

import typer

from sahayak.api.app import create_app
from sahayak.config import Settings


def openapi_command(
    out: Annotated[Path, typer.Option(help="Where to write the schema (- for stdout).")] = Path(
        "web/openapi.json"
    ),
) -> None:
    """Write the OpenAPI schema without starting any service (no DB/Redis/provider calls)."""
    app = create_app(Settings(_env_file=None))
    text = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    if str(out) == "-":
        typer.echo(text)
    else:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Wrote {out}")

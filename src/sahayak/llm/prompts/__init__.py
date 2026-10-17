"""Versioned prompt templates (TRD §17: prompts live in files, not inline strings).

Each prompt is `<name>.v<N>.md`: YAML front matter (`name`, `version`, `description`) followed by a
`## system` section and a `## user` section, both Jinja2 templates. Changing a prompt's behaviour
means adding a new version file, so stored outputs (for example draft rules) can record exactly
which prompt produced them.
"""

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined, Template, select_autoescape

_FILENAME_RE = re.compile(r"^(?P<name>[a-z][a-z0-9_]*)\.v(?P<version>\d+)\.md$")
_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_SECTION_RE = re.compile(r"^## (system|user)[ \t]*$", re.MULTILINE)

# Plain-text prompts: nothing is HTML-escaped. Missing variables are errors, never blanks.
_env = Environment(
    undefined=StrictUndefined,
    autoescape=select_autoescape(enabled_extensions=(), default_for_string=False, default=False),
    keep_trailing_newline=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


class PromptError(Exception):
    """A prompt file is missing or malformed."""


@dataclass(frozen=True)
class RenderedPrompt:
    id: str  # "<name>@v<version>", stored as provenance next to LLM outputs
    system: str
    user: str


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    description: str
    system: Template
    user: Template

    @property
    def id(self) -> str:
        return f"{self.name}@v{self.version}"

    def render(self, **variables: Any) -> RenderedPrompt:
        return RenderedPrompt(
            id=self.id,
            system=self.system.render(**variables).strip(),
            user=self.user.render(**variables).strip(),
        )


def available_prompts() -> dict[str, list[int]]:
    """Prompt name -> sorted versions found in this package."""
    found: dict[str, list[int]] = {}
    for entry in resources.files(__name__).iterdir():
        match = _FILENAME_RE.match(entry.name)
        if match:
            found.setdefault(match["name"], []).append(int(match["version"]))
    return {name: sorted(versions) for name, versions in found.items()}


@cache
def load_prompt(name: str, version: int | None = None) -> Prompt:
    """Load a prompt; `version=None` means the highest version available."""
    versions = available_prompts().get(name)
    if not versions:
        raise PromptError(f"no prompt named {name!r}")
    chosen = versions[-1] if version is None else version
    if chosen not in versions:
        raise PromptError(f"prompt {name!r} has no version {chosen} (have {versions})")
    filename = f"{name}.v{chosen}.md"
    return parse_prompt(filename, resources.files(__name__).joinpath(filename).read_text("utf-8"))


def parse_prompt(filename: str, text: str) -> Prompt:
    """Parse one prompt file. The file name must look like `<name>.v<N>.md`."""
    name_match = _FILENAME_RE.match(filename)
    if not name_match:
        raise PromptError(f"{filename}: prompt files are named <name>.v<N>.md")
    name, version = name_match["name"], int(name_match["version"])
    match = _FRONT_MATTER_RE.match(text.replace("\r\n", "\n"))
    if not match:
        raise PromptError(f"{filename}: missing YAML front matter")
    meta = yaml.safe_load(match["meta"]) or {}
    if meta.get("name") != name or meta.get("version") != version:
        raise PromptError(f"{filename}: front matter name/version must match the file name")

    parts = _SECTION_RE.split(match["body"])
    # split() yields [preamble, "system", text, "user", text]
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    if parts[1::2] != ["system", "user"]:
        raise PromptError(f"{filename}: needs exactly one '## system' then one '## user' section")
    return Prompt(
        name=name,
        version=version,
        description=str(meta.get("description", "")),
        system=_env.from_string(sections["system"]),
        user=_env.from_string(sections["user"]),
    )

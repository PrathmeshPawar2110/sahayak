"""Per-node tracing for the agent graph (TRD §5 Observability, M4-T14).

Each node runs in an OpenTelemetry span `agent.<node>` and logs an `agent_node` event with its
latency and the LLM calls, tokens and cost it caused. Neither carries message text or profile
values: only counts, names and timings.
"""

import functools
import time
from collections.abc import Awaitable
from typing import Protocol

import structlog
from langgraph.runtime import Runtime
from opentelemetry import trace

from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState
from sahayak.observability.usage import metered

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class Node(Protocol):
    """A node of the agent graph. LangGraph passes `runtime` by keyword."""

    def __call__(
        self, state: AgentState, *, runtime: Runtime[AgentDeps]
    ) -> Awaitable[AgentState]: ...


def traced_node(name: str, node: Node) -> Node:
    @functools.wraps(node)
    async def run(state: AgentState, *, runtime: Runtime[AgentDeps]) -> AgentState:
        with tracer.start_as_current_span(f"agent.{name}") as span, metered() as usage:
            started = time.perf_counter()
            status = "error"
            try:
                update = await node(state, runtime=runtime)
                status = "ok"
                return update
            finally:
                latency_ms = round((time.perf_counter() - started) * 1000)
                fields = usage.fields()
                span.set_attribute("agent.node", name)
                span.set_attribute("agent.latency_ms", latency_ms)
                for key, value in fields.items():
                    if value is not None:
                        span.set_attribute(f"agent.{key}", value)
                log.info("agent_node", node=name, status=status, latency_ms=latency_ms, **fields)

    return run

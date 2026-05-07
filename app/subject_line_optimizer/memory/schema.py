"""Pattern types surfaced from long-term memory for this user.

The agent never writes raw subject lines or briefings to long-term namespaces.
Instead, AgentCore's SEMANTIC, USER_PREFERENCE, SUMMARIZATION, and EPISODIC
strategies extract patterns asynchronously from session events. This module
defines the typed view the iteration loop sees when it asks "what do we
already know about this user?"
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pattern:
    """A pattern surfaced from a memory namespace at retrieval time."""

    namespace: str
    content: str
    score: float


@dataclass
class MemoryContext:
    """Structured input to the generator built from long-term memory hits.

    Empty when running without Memory configured. Empty also for new users
    whose strategies haven't extracted anything yet (first session, or
    within ~60s of a prior session before async extraction completes).
    """

    facts: list[Pattern] = field(default_factory=list)
    preferences: list[Pattern] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.facts and not self.preferences

    def render_for_prompt(self) -> str:
        """Format the context as a short bullet list for inclusion in prompts."""
        if self.is_empty:
            return ""
        lines = []
        if self.facts:
            lines.append("Patterns observed across this user's prior sessions:")
            for p in self.facts:
                lines.append(f"- {p.content}")
        if self.preferences:
            lines.append("Stated preferences from this user:")
            for p in self.preferences:
                lines.append(f"- {p.content}")
        return "\n".join(lines)

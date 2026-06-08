"""Sequential agent chain with tool-based control flow.

Supported agent types
---------------------
- ``"agent"``      — plain agent, no injected tools
- ``"guardrail"``  — gets ``block(reason)`` / ``allow()``
- ``"router"``     — gets ``route(agent_name, reason)``
- ``"evaluator"``  — gets ``approve()`` / ``revise(feedback)``
- ``"fallback"``   — gets ``fail(reason)``
"""

from __future__ import annotations

from edge_agent.agent import Agent
from edge_agent.logger import get_logger
from edge_agent.tool import Tool
from edge_agent.types import AgentStep, RunResult

# ── decision containers ─────────────────────────────────────────────────────


class _GuardDecision:
    __slots__ = ("blocked", "reason")

    def __init__(self) -> None:
        self.blocked = False
        self.reason = ""

    def reset(self) -> None:
        self.blocked = False
        self.reason = ""


class _RouterDecision:
    __slots__ = ("target_agent", "reason")

    def __init__(self) -> None:
        self.target_agent = ""
        self.reason = ""

    def reset(self) -> None:
        self.target_agent = ""
        self.reason = ""


class _EvalDecision:
    __slots__ = ("approved", "feedback")

    def __init__(self) -> None:
        self.approved = False
        self.feedback = ""

    def reset(self) -> None:
        self.approved = False
        self.feedback = ""


class _FallbackDecision:
    __slots__ = ("failed", "reason")

    def __init__(self) -> None:
        self.failed = False
        self.reason = ""

    def reset(self) -> None:
        self.failed = False
        self.reason = ""


# ── tool factories ──────────────────────────────────────────────────────────


def _make_guard_tools(d: _GuardDecision) -> list[Tool]:
    def block(reason: str) -> str:
        """Block the current request from proceeding to the next agent."""
        d.blocked = True
        d.reason = reason
        return f"Blocked: {reason}"

    def allow() -> str:
        """Allow the current request to proceed to the next agent."""
        d.blocked = False
        return "Allowed."

    return [
        Tool(fn=block, name="block", description=block.__doc__,
             parameters={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}),
        Tool(fn=allow, name="allow", description=allow.__doc__,
             parameters={"type": "object", "properties": {}}),
    ]


def _make_router_tools(d: _RouterDecision, agent_names: list[str]) -> list[Tool]:
    names_str = ", ".join(f'"{n}"' for n in agent_names)

    def route(agent_name: str, reason: str) -> str:
        f"""Route the request to one of the available agents: {names_str}."""
        d.target_agent = agent_name
        d.reason = reason
        return f"Routing to {agent_name}: {reason}"

    desc = f"Route the request to one of the available agents: {names_str}."
    return [
        Tool(fn=route, name="route", description=desc,
             parameters={"type": "object",
                          "properties": {
                              "agent_name": {"type": "string", "description": f"One of: {names_str}"},
                              "reason": {"type": "string"},
                          },
                          "required": ["agent_name", "reason"]}),
    ]


def _make_eval_tools(d: _EvalDecision) -> list[Tool]:
    def approve() -> str:
        """Approve the output — it meets quality standards."""
        d.approved = True
        return "Approved."

    def revise(feedback: str) -> str:
        """Request a revision with specific feedback on what to improve."""
        d.approved = False
        d.feedback = feedback
        return f"Revision requested: {feedback}"

    return [
        Tool(fn=approve, name="approve", description=approve.__doc__,
             parameters={"type": "object", "properties": {}}),
        Tool(fn=revise, name="revise", description=revise.__doc__,
             parameters={"type": "object", "properties": {"feedback": {"type": "string"}}, "required": ["feedback"]}),
    ]


def _make_fallback_tools(d: _FallbackDecision) -> list[Tool]:
    def fail(reason: str) -> str:
        """Signal that you cannot handle this request so the next agent can try."""
        d.failed = True
        d.reason = reason
        return f"Failed: {reason}"

    return [
        Tool(fn=fail, name="fail", description=fail.__doc__,
             parameters={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}),
    ]


# ── chain ───────────────────────────────────────────────────────────────────


class Chain:
    """Runs multiple agents sequentially with tool-based control flow.

    Each agent's ``agent_type`` determines what tools are injected and how
    the chain reacts:

    - ``"agent"``      — runs normally, no special tools
    - ``"guardrail"``  — ``block`` / ``allow``; blocks halt the chain
    - ``"router"``     — ``route``; skips to the named agent
    - ``"evaluator"``  — ``approve`` / ``revise``; can loop back to the
      previous agent for refinement (up to *max_revisions* times)
    - ``"fallback"``   — ``fail``; on failure the chain tries the next agent
    """

    def __init__(
        self,
        *,
        agents: list[Agent],
        pass_original: bool = True,
        max_revisions: int = 3,
    ) -> None:
        if not agents:
            raise ValueError("Chain requires at least one agent.")
        self.agents = agents
        self.pass_original = pass_original
        self.max_revisions = max_revisions

        self._guard = _GuardDecision()
        self._guard_tools = _make_guard_tools(self._guard)
        self._router = _RouterDecision()
        self._eval = _EvalDecision()
        self._eval_tools = _make_eval_tools(self._eval)
        self._fallback = _FallbackDecision()
        self._fallback_tools = _make_fallback_tools(self._fallback)
        self._logger = get_logger("chain")

    def run(self, user_message: str, *, max_turns: int = 10) -> RunResult:
        """Execute the chain and return a :class:`RunResult`."""
        current_input = user_message
        agents = self.agents
        idx = 0
        prev_output = ""
        all_steps: list[AgentStep] = []

        while idx < len(agents):
            agent = agents[idx]
            self._logger.info("chain → agent %r (%s)", agent.name, agent.agent_type)

            if agent.agent_type == "guardrail":
                run_result = self._run_guardrail(agent, current_input, max_turns)
                all_steps.extend(run_result.steps)
                if self._guard.blocked:
                    return RunResult(output=run_result.output, steps=all_steps)
                output = run_result.output

            elif agent.agent_type == "router":
                remaining = [a.name for a in agents[idx + 1:]]
                run_result = self._run_router(agent, current_input, max_turns, remaining)
                all_steps.extend(run_result.steps)
                if self._router.target_agent:
                    target = self._router.target_agent
                    target_idx = self._find_agent(target, start=idx + 1)
                    if target_idx is not None:
                        routed = agents[target_idx]
                        self._logger.info("chain → routed to %r", routed.name)
                        routed_result = routed.run(current_input, max_turns=max_turns)
                        all_steps.extend(routed_result.steps)
                        return RunResult(output=routed_result.output, steps=all_steps)
                    self._logger.warning("router target %r not found, continuing", target)
                output = run_result.output

            elif agent.agent_type == "evaluator":
                eval_result = self._run_evaluator(
                    agent, prev_output, current_input, idx, max_turns,
                )
                all_steps.extend(eval_result.steps)
                output = eval_result.output

            elif agent.agent_type == "fallback":
                run_result = self._run_fallback(agent, current_input, max_turns)
                all_steps.extend(run_result.steps)
                if self._fallback.failed:
                    idx += 1
                    continue
                return RunResult(output=run_result.output, steps=all_steps)

            else:
                run_result = agent.run(current_input, max_turns=max_turns)
                all_steps.extend(run_result.steps)
                output = run_result.output

            prev_output = output
            if not self.pass_original:
                current_input = output
            idx += 1

        return RunResult(output=prev_output, steps=all_steps)

    # -- type-specific runners ------------------------------------------------

    def _run_guardrail(
        self, agent: Agent, msg: str, max_turns: int,
    ) -> RunResult:
        self._guard.reset()
        result = agent.run(msg, max_turns=max_turns, extra_tools=self._guard_tools)
        if self._guard.blocked:
            self._logger.info("agent %r blocked: %s", agent.name, self._guard.reason)
        return result

    def _run_router(
        self, agent: Agent, msg: str, max_turns: int, agent_names: list[str],
    ) -> RunResult:
        self._router.reset()
        tools = _make_router_tools(self._router, agent_names)
        result = agent.run(msg, max_turns=max_turns, extra_tools=tools)
        if self._router.target_agent:
            self._logger.info(
                "agent %r routed to %r: %s",
                agent.name, self._router.target_agent, self._router.reason,
            )
        return result

    def _run_evaluator(
        self,
        evaluator: Agent,
        prev_output: str,
        original_input: str,
        eval_idx: int,
        max_turns: int,
    ) -> RunResult:
        prev_agent = self.agents[eval_idx - 1] if eval_idx > 0 else None
        current_output = prev_output
        eval_steps: list[AgentStep] = []

        for attempt in range(self.max_revisions + 1):
            self._eval.reset()
            eval_input = f"Review this output:\n\n{current_output}"
            eval_result = evaluator.run(
                eval_input, max_turns=max_turns, extra_tools=self._eval_tools,
            )
            eval_steps.extend(eval_result.steps)

            if self._eval.approved:
                self._logger.info("agent %r approved output", evaluator.name)
                return RunResult(output=current_output, steps=eval_steps)

            if prev_agent is None:
                self._logger.warning("evaluator has no previous agent to revise")
                return RunResult(output=current_output, steps=eval_steps)

            if attempt < self.max_revisions:
                self._logger.info(
                    "agent %r requested revision (%d/%d): %s",
                    evaluator.name, attempt + 1, self.max_revisions,
                    self._eval.feedback,
                )
                revision_prompt = (
                    f"{original_input}\n\n"
                    f"Previous attempt:\n{current_output}\n\n"
                    f"Feedback: {self._eval.feedback}"
                )
                revision_result = prev_agent.run(
                    revision_prompt, max_turns=max_turns,
                )
                eval_steps.extend(revision_result.steps)
                current_output = revision_result.output

        self._logger.warning("max revisions (%d) reached", self.max_revisions)
        return RunResult(output=current_output, steps=eval_steps)

    def _run_fallback(
        self, agent: Agent, msg: str, max_turns: int,
    ) -> RunResult:
        self._fallback.reset()
        result = agent.run(msg, max_turns=max_turns, extra_tools=self._fallback_tools)
        if self._fallback.failed:
            self._logger.info(
                "agent %r failed: %s", agent.name, self._fallback.reason,
            )
        return result

    # -- helpers --------------------------------------------------------------

    def _find_agent(self, name: str, *, start: int = 0) -> int | None:
        for i in range(start, len(self.agents)):
            if self.agents[i].name == name:
                return i
        return None

"""Bounded JSON-action loop: the model chooses tool calls each turn and finishes with a Verdict."""
import json
from dataclasses import dataclass, field

from agent.prompts import (FIRST_MESSAGE, FORCE_FINISH, REVIEW_MESSAGE, SYSTEM_PROMPT, TOOL_RESULTS_MESSAGE,
                           TOOLS_HEADER, VALIDATION_MESSAGE)
from agent.schema import AgentStep
from llm.jsoncall import JsonCallError, call_json
from tools import TOOL_SPECS, ToolError

MAX_RESULT_CHARS = 6000  # per turn's tool output; the whole history is re-sent every turn


@dataclass
class InvestigationResult:
    verdict: object = None
    turns: int = 0
    forced_finish: bool = False
    completions: list = field(default_factory=list)
    transcript: list = field(default_factory=list)   # (thought, calls) per turn, for audit/debug
    flags: list = field(default_factory=list)
    validation_rounds: int = 0
    reviewed: bool = False
    review_challenges: list = field(default_factory=list)


def _shrink(obj, cap=4000):
    """Cut big values before serialising (security review F2: a 900 MB tool value must not be stringified)."""
    if isinstance(obj, dict):
        return {k: _shrink(v, cap) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_shrink(v, cap) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, str) and len(obj) > cap:
        return obj[:cap] + "...[cut]"
    return obj


def _dump(obj, limit=MAX_RESULT_CHARS):
    # < and > are escaped so evidence text cannot close the <case_data> wrapper (security review F11).
    text = json.dumps(_shrink(obj), ensure_ascii=False, default=str).replace("<", "\\u003c").replace(">", "\\u003e")
    return text if len(text) <= limit else text[:limit] + f"... [truncated {len(text) - limit} chars]"


def _tools_text():
    return TOOLS_HEADER + "\n".join(f"- {name}: {desc}" for name, desc in TOOL_SPECS.items())


def investigate(gateway, toolbox, max_turns=6, extra_context=None, validate=None, max_validation_rounds=2,
                review=None):
    """validate: callable(verdict) -> [problems]; problems are sent back for revision (bounded rounds).
    review: callable(verdict) -> [challenges] (the skeptic); called once, after the first verdict that passes
    validation. Challenges go back to the investigator, who re-examines and keeps the final say."""
    case = toolbox.preload()
    if extra_context:
        case = {**case, **extra_context}
    messages = [{"role": "user", "content": FIRST_MESSAGE.format(case_json=_dump(case, 40000), tools=_tools_text())}]
    result = InvestigationResult()
    tool_turns = 0
    for _ in range(max_turns + 4 + 2 * max_validation_rounds):
        try:
            step = call_json(gateway, SYSTEM_PROMPT, messages, AgentStep, max_retries=2)
        except JsonCallError as exc:
            result.completions.extend(exc.completions)
            result.flags.append(f"agent reply invalid after retries: {exc}"[:300])
            return result
        result.completions.extend(step.completions)
        result.turns += 1
        action = step.value
        result.transcript.append({"thought": action.thought,
                                  "calls": [c.model_dump() for c in action.calls]})
        if action.action == "finish":
            result.verdict = action.verdict
            problems = validate(action.verdict) if validate else []
            if problems:
                if result.validation_rounds >= max_validation_rounds:
                    result.flags.extend(f"validator: {p}" for p in problems)
                    return result
                result.validation_rounds += 1
                feedback = VALIDATION_MESSAGE.format(problems="\n".join(f"- {p}" for p in problems))
            elif review is not None and not result.reviewed:
                result.reviewed = True
                challenges = review(action.verdict)
                result.review_challenges = list(challenges)
                if not challenges:
                    return result
                tool_turns = max(0, max_turns - 1)  # one more tool turn allowed to check the challenges
                feedback = REVIEW_MESSAGE.format(challenges="\n".join(f"- {c}" for c in challenges))
            else:
                return result
            messages = messages + [{"role": "assistant", "content": action.model_dump_json()},
                                   {"role": "user", "content": feedback}]
            continue
        outputs = []
        for call in action.calls:
            try:
                outputs.append({"tool": call.tool, "args": call.args, "result": toolbox.call(call.tool, call.args)})
            except ToolError as exc:
                outputs.append({"tool": call.tool, "args": call.args, "error": str(exc)})
            except Exception as exc:  # never let a tool crash the case (security review F5)
                outputs.append({"tool": call.tool, "args": call.args,
                                "error": f"tool failed: {type(exc).__name__}"})
        tool_turns += 1
        reminder = ""
        if tool_turns >= max_turns:
            reminder = FORCE_FINISH
            result.forced_finish = True
        messages = messages + [
            {"role": "assistant", "content": action.model_dump_json()},
            {"role": "user", "content": TOOL_RESULTS_MESSAGE.format(results_json=_dump(outputs), reminder=reminder)},
        ]
    result.flags.append("agent did not finish after forced-finish turn")
    return result

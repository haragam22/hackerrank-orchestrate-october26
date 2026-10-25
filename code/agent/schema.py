"""Structured outputs the model must return. Every reply is validated against these models."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Treatment = Literal["DELETE", "REDACT", "ANONYMIZE", "RETAIN", "BLOCK_RESTORE", "DISABLE_FLOW"]


class ResourceVerdict(BaseModel):
    resource_id: str
    system_id: str
    data_category: str = Field(description="deletion_rules.csv category, or DATA_FLOW for integrations/connectors")
    rule_id: str = Field(description="rule_id applied, or '' for DATA_FLOW")
    treatment: Treatment = Field(description="treatment the policy requires for this resource")
    compliant: bool = Field(description="true if, at audit_at, the resource already has the required treatment "
                                        "(deleted / redacted / anonymized / retained / restore blocked / flow disabled)")
    evidence_id: str = Field(description="file_id / activity_id / audit_id (<= audit_at) proving the state")
    reason: str = Field(description="one sentence quoting the evidence")


class Verdict(BaseModel):
    intent: Literal["DELETION", "NOT_DELETION"]
    intent_reason: str
    resources: list[ResourceVerdict] = Field(default_factory=list,
                                             description="every in-scope resource; empty for NOT_DELETION")
    flags: list[str] = Field(default_factory=list, description="uncertainties a human should review")


class ToolCall(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)


class AgentStep(BaseModel):
    action: Literal["call_tools", "finish"]
    thought: str = Field(default="", description="one short sentence: why this step")
    calls: list[ToolCall] = Field(default_factory=list, description="1-6 tool calls when action=call_tools")
    verdict: Verdict | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.action == "call_tools" and not (1 <= len(self.calls) <= 6):
            raise ValueError("call_tools needs 1-6 calls")
        if self.action == "finish" and self.verdict is None:
            raise ValueError("finish needs a verdict")
        return self

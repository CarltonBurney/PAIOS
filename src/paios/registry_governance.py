"""Policy-governed registry mutations (§3B).

Registry lifecycle changes are themselves governed requests. Before this
module, the Registry Service *audited* mutations — it recorded who promoted
what, but nothing evaluated whether they were allowed to. Auditing a change is
not governing it.

Every lifecycle action — register, create_version, validate, promote,
deprecate, retire, suspend, resume, transfer_ownership — is evaluated by the
same policy engine that governs execution requests, using the same decision
enum and the same precedence:

    deny > require_approval > allow_with_controls > allow

Separation of duties is enforced *after* policy: where a mutation requires
approval, the requester and the approver may not be the same principal. A
policy that demands approval is satisfied by a real second party, never by the
requester approving their own change.

This module owns the dependency on the policy engine. `registry` defines only
the `MutationGovernor` protocol, so the two stay independently testable and the
registry never imports policy.
"""

from __future__ import annotations

from pathlib import Path

from .models import (
    Classification,
    Identity,
    PolicyOutcome,
    Request,
    RequestType,
    RiskAssessment,
    RiskDomain,
    RiskLevel,
)
from .policy import PolicyEngine, PolicySet
from .registry import MutationContext, MutationVerdict

# Reason codes emitted by this layer rather than by a policy document.
SEPARATION_OF_DUTIES = "SEPARATION_OF_DUTIES"
APPROVAL_REQUIRED = "REGISTRY_APPROVAL_REQUIRED"
APPROVAL_NOT_GRANTED = "REGISTRY_APPROVAL_NOT_GRANTED"

# Registry mutations are not classified from natural language; they arrive as
# structured operations. A fixed classification keeps them inside the same
# policy vocabulary without pretending a classifier ran.
_MUTATION_CLASSIFICATION = Classification(
    request_type=RequestType.GOVERNANCE_CHANGE,
    confidence=1.0,
    signals=("registry_mutation",),
)


class RegistryGovernance:
    """Evaluates proposed registry mutations against a policy set."""

    def __init__(self, policy_set: PolicySet, *, environment: str = "dev") -> None:
        self.engine = PolicyEngine(policy_set)
        self.environment = environment

    @classmethod
    def from_file(
        cls, path: str | Path, *, environment: str = "dev"
    ) -> RegistryGovernance:
        return cls(PolicySet.from_file(path), environment=environment)

    def evaluate(self, context: MutationContext) -> MutationVerdict:
        decision = self.engine.evaluate(
            _as_request(context),
            _MUTATION_CLASSIFICATION,
            _as_risk(context),
            environment=context.target_environment or self.environment,
            attributes=_as_attributes(context),
        )

        base = MutationVerdict(
            allowed=True,
            decision=decision.decision.value,
            matched=decision.matched,
            reason_codes=decision.reason_codes,
        )

        if decision.decision is PolicyOutcome.DENY:
            codes = ", ".join(decision.reason_codes) or "policy denied"
            return MutationVerdict(
                allowed=False,
                decision=decision.decision.value,
                matched=decision.matched,
                reason_codes=decision.reason_codes,
                detail=(
                    f"{context.operation.value} of '{context.resource_id}' denied: "
                    f"{codes}"
                ),
            )

        if decision.decision is PolicyOutcome.REQUIRE_APPROVAL:
            if not context.approval_granted:
                return MutationVerdict(
                    allowed=False,
                    decision=decision.decision.value,
                    matched=decision.matched,
                    reason_codes=decision.reason_codes + (APPROVAL_REQUIRED,),
                    detail=(
                        f"{context.operation.value} of '{context.resource_id}' "
                        "requires approval and none was granted"
                    ),
                )
            # Separation of duties: a second party must sign off.
            if context.approver and context.approver == context.principal:
                return MutationVerdict(
                    allowed=False,
                    decision=PolicyOutcome.DENY.value,
                    matched=decision.matched,
                    reason_codes=decision.reason_codes + (SEPARATION_OF_DUTIES,),
                    detail=(
                        f"principal '{context.principal}' cannot approve their own "
                        f"{context.operation.value} of '{context.resource_id}'"
                    ),
                )

        return base


def _as_request(context: MutationContext) -> Request:
    """Wrap the mutation's principal so the policy engine sees an identity."""
    return Request(
        content=f"{context.operation.value} {context.resource_id}",
        identity=Identity(
            subject=context.principal,
            roles=context.principal_roles,
            groups=context.principal_groups,
            authenticated=True,
        ),
        metadata={"registry_mutation": context.to_dict()},
    )


def _as_risk(context: MutationContext) -> RiskAssessment:
    """The resource's own risk carries into the mutation decision.

    Promoting an L4 capability is a higher-impact act than promoting an L0 one,
    so the resource's declared risk is the mutation's risk. Resources with no
    declared risk (or registries that do not model it) fall back to L2 — a
    lifecycle change is never routine.
    """
    level = RiskLevel(context.resource_risk_level) if context.resource_risk_level else (
        RiskLevel.L2
    )
    domains = {RiskDomain(d) for d in context.resource_risk_domains}
    domains.add(RiskDomain.GOVERNANCE)
    return RiskAssessment(
        level=level,
        domains=frozenset(domains),
        triggers=(f"registry:{context.operation.value}",),
    )


def _as_attributes(context: MutationContext) -> dict[str, frozenset[str]]:
    """Candidate sets for the generalized attribute predicates."""
    attributes: dict[str, frozenset[str]] = {
        "registry_operation": frozenset({context.operation.value}),
        "registry_type": frozenset({context.registry_type.value}),
        "resource_environments": context.resource_environments,
    }
    if context.target_environment:
        attributes["target_environment"] = frozenset({context.target_environment})
    else:
        attributes["target_environment"] = frozenset()
    if context.resource_status is not None:
        attributes["resource_status"] = frozenset({context.resource_status.value})
    return attributes

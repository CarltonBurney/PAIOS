"""Policy decision precedence and domain-matching semantics.

Covers required tests 1-7: domain overlap matching, decision precedence, and
the least-privilege tool merge.
"""

from __future__ import annotations

import pytest

from paios import (
    Classification,
    Identity,
    PolicyEngine,
    PolicyOutcome,
    PolicySet,
    Request,
    RequestType,
    RiskAssessment,
    RiskDomain,
    RiskLevel,
)


def policy_set(*policies) -> PolicySet:
    return PolicySet.from_dict(
        {"policySetName": "test", "description": "", "policies": list(policies)}
    )


def policy(pid: str, decision: str, priority: int = 0, **effects):
    return {
        "policy_id": pid,
        "enabled": True,
        "priority": priority,
        "scope": {"departments": ["*"], "agents": ["*"], "environments": ["*"]},
        "conditions": {},
        "effects": {"decision": decision, **effects},
    }


def risk(level: RiskLevel, *domains: RiskDomain) -> RiskAssessment:
    return RiskAssessment(level=level, domains=frozenset(domains))


def request(**identity_kwargs) -> Request:
    defaults = {"subject": "alice@contoso.com", "authenticated": True}
    defaults.update(identity_kwargs)
    roles = defaults.pop("roles", frozenset())
    return Request(content="x", identity=Identity(roles=frozenset(roles), **defaults))


CLASSIFICATION = Classification(request_type=RequestType.CORE, confidence=1.0)


def evaluate(pset: PolicySet, assessment: RiskAssessment, **kwargs):
    return PolicyEngine(pset).evaluate(
        kwargs.pop("req", request()), CLASSIFICATION, assessment, **kwargs
    )


class TestDomainMatching:
    def test_security_policy_matches_security_plus_compliance_request(self):
        """Required test 1 — overlap, not exact equality."""
        pset = policy_set(
            {
                **policy("SEC", "require_approval"),
                "conditions": {"risk_domains": ["security"]},
            }
        )
        decision = evaluate(
            pset, risk(RiskLevel.L3, RiskDomain.SECURITY, RiskDomain.COMPLIANCE)
        )
        assert "SEC" in decision.matched

    def test_unrelated_domains_do_not_match(self):
        """Required test 2 — empty intersection means no match."""
        pset = policy_set(
            {
                **policy("SEC", "deny"),
                "conditions": {"risk_domains": ["security"]},
            }
        )
        decision = evaluate(
            pset, risk(RiskLevel.L4, RiskDomain.FINANCIAL, RiskDomain.OPERATIONAL)
        )
        assert decision.matched == ()
        assert decision.decision is PolicyOutcome.ALLOW

    def test_empty_domain_condition_matches_anything(self):
        pset = policy_set(policy("BASE", "allow"))
        assert "BASE" in evaluate(pset, risk(RiskLevel.L0)).matched

    def test_single_shared_domain_is_enough(self):
        pset = policy_set(
            {
                **policy("MULTI", "require_approval"),
                "conditions": {"risk_domains": ["security", "privacy"]},
            }
        )
        decision = evaluate(pset, risk(RiskLevel.L2, RiskDomain.PRIVACY))
        assert "MULTI" in decision.matched


class TestDecisionPrecedence:
    def test_any_deny_beats_all_allows(self):
        """Required test 3."""
        pset = policy_set(
            policy("A1", "allow"),
            policy("A2", "allow"),
            policy("D", "deny", reason_code="PROHIBITED_OPERATION"),
            policy("A3", "allow_with_controls"),
        )
        decision = evaluate(pset, risk(RiskLevel.L0))
        assert decision.decision is PolicyOutcome.DENY
        assert "PROHIBITED_OPERATION" in decision.reason_codes

    def test_deny_beats_require_approval(self):
        """Required test 4 — approval must never override deny."""
        pset = policy_set(
            policy("APPROVE", "require_approval", priority=999),
            policy("DENY", "deny", priority=1),
        )
        decision = evaluate(pset, risk(RiskLevel.L0))
        assert decision.decision is PolicyOutcome.DENY
        assert decision.denied
        assert not decision.require_approval

    def test_require_approval_beats_controlled_allow(self):
        """Required test 5."""
        pset = policy_set(
            policy("CONTROLLED", "allow_with_controls"),
            policy("APPROVE", "require_approval"),
        )
        assert evaluate(pset, risk(RiskLevel.L0)).decision is (
            PolicyOutcome.REQUIRE_APPROVAL
        )

    def test_controlled_allow_beats_plain_allow(self):
        pset = policy_set(policy("A", "allow"), policy("C", "allow_with_controls"))
        assert evaluate(pset, risk(RiskLevel.L0)).decision is (
            PolicyOutcome.ALLOW_WITH_CONTROLS
        )

    def test_precedence_is_independent_of_priority_order(self):
        high_first = policy_set(
            policy("DENY", "deny", priority=500),
            policy("APPROVE", "require_approval", priority=1),
        )
        low_first = policy_set(
            policy("APPROVE", "require_approval", priority=500),
            policy("DENY", "deny", priority=1),
        )
        assert (
            evaluate(high_first, risk(RiskLevel.L0)).decision
            is evaluate(low_first, risk(RiskLevel.L0)).decision
            is PolicyOutcome.DENY
        )

    def test_disabled_policy_does_not_apply(self):
        raw = policy("DENY", "deny")
        raw["enabled"] = False
        decision = evaluate(policy_set(raw, policy("A", "allow")), risk(RiskLevel.L0))
        assert decision.decision is PolicyOutcome.ALLOW

    @pytest.mark.parametrize(
        ("decisions", "expected"),
        [
            (["allow"], PolicyOutcome.ALLOW),
            (["allow", "allow_with_controls"], PolicyOutcome.ALLOW_WITH_CONTROLS),
            (["allow_with_controls", "require_approval"], PolicyOutcome.REQUIRE_APPROVAL),
            (["require_approval", "deny"], PolicyOutcome.DENY),
            (["deny", "allow"], PolicyOutcome.DENY),
        ],
    )
    def test_precedence_table(self, decisions, expected):
        pset = policy_set(
            *(policy(f"P{i}", d) for i, d in enumerate(decisions))
        )
        assert evaluate(pset, risk(RiskLevel.L0)).decision is expected


class TestToolMerge:
    def test_allowed_tool_intersection_cannot_widen_access(self):
        """Required test 6 — adding a policy never grants more."""
        narrow = policy_set(
            policy("A", "allow", allowed_tools=["read", "write"]),
            policy("B", "allow", allowed_tools=["read"]),
        )
        decision = evaluate(narrow, risk(RiskLevel.L0))

        assert decision.allowed_tools == frozenset({"read"})
        assert decision.permits_tool("read")
        assert not decision.permits_tool("write")

    def test_adding_a_policy_never_grants_a_new_tool(self):
        before = evaluate(
            policy_set(policy("A", "allow", allowed_tools=["read"])),
            risk(RiskLevel.L0),
        )
        after = evaluate(
            policy_set(
                policy("A", "allow", allowed_tools=["read"]),
                policy("B", "allow", allowed_tools=["export"]),
            ),
            risk(RiskLevel.L0),
        )
        assert before.permits_tool("read")
        assert not after.permits_tool("export")

    def test_denied_tool_union_always_restricts(self):
        """Required test 7 — a denial anywhere is a denial everywhere."""
        pset = policy_set(
            policy("A", "allow", allowed_tools=["read", "export"]),
            policy("B", "allow", denied_tools=["export"]),
        )
        decision = evaluate(pset, risk(RiskLevel.L0))

        assert decision.permits_tool("read")
        assert not decision.permits_tool("export")

    def test_deny_wildcard_refuses_every_tool(self):
        pset = policy_set(policy("D", "deny", denied_tools=["*"]))
        decision = evaluate(pset, risk(RiskLevel.L0))
        assert not decision.permits_tool("anything")

    def test_a_denied_decision_permits_nothing(self):
        pset = policy_set(
            policy("A", "allow", allowed_tools=["read"]),
            policy("D", "deny"),
        )
        assert not evaluate(pset, risk(RiskLevel.L0)).permits_tool("read")

    def test_audit_level_takes_the_maximum(self):
        pset = policy_set(
            policy("A", "allow", audit_level="minimal"),
            policy("B", "allow", audit_level="full"),
            policy("C", "allow", audit_level="standard"),
        )
        assert evaluate(pset, risk(RiskLevel.L0)).audit_level == "full"


class TestRoleConditions:
    def test_policy_fires_when_principal_lacks_every_listed_role(self):
        pset = policy_set(
            {
                **policy("GOV", "deny", reason_code="GOVERNANCE_ROLE_REQUIRED"),
                "conditions": {"principal_roles_none_of": ["governance_admin"]},
            }
        )
        decision = evaluate(pset, risk(RiskLevel.L0), req=request())
        assert decision.denied

    def test_policy_does_not_fire_when_principal_holds_the_role(self):
        pset = policy_set(
            {
                **policy("GOV", "deny"),
                "conditions": {"principal_roles_none_of": ["governance_admin"]},
            }
        )
        decision = evaluate(
            pset, risk(RiskLevel.L0), req=request(roles={"governance_admin"})
        )
        assert not decision.denied


class TestRiskNeverAuthorizes:
    def test_low_risk_does_not_bypass_a_deny(self):
        """The design rule: risk is an input, never a shortcut."""
        pset = policy_set(policy("D", "deny", reason_code="PROHIBITED_OPERATION"))
        decision = evaluate(pset, risk(RiskLevel.L0))

        assert decision.denied
        assert not decision.permits_tool("anything")

    def test_low_risk_does_not_grant_an_unlisted_tool(self):
        pset = policy_set(policy("A", "allow", allowed_tools=["read"]))
        decision = evaluate(pset, risk(RiskLevel.L0))
        assert not decision.permits_tool("delete_everything")

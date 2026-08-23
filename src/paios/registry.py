"""Governed registry framework.

PAIOS registries are not JSON catalogs. Agent, Model, Tool, and Workflow
registries share one governance lifecycle while keeping resource-specific
schemas, achieved by **composition** rather than a universal schema::

    GovernedResourceMetadata  +  ToolSpec       ->  ToolDefinition
    GovernedResourceMetadata  +  AgentSpec      ->  AgentDefinition
    GovernedResourceMetadata  +  ModelSpec      ->  ModelDefinition
    GovernedResourceMetadata  +  WorkflowSpec   ->  WorkflowDefinition

A single ``RegistryThing(tool_fields=None, model_fields=None, ...)`` would
degrade into a bag of optional fields, so it is deliberately not that.

DESIGN RULE — registry activation is not authorization.
-------------------------------------------------------
An ``active`` resource is *operationally available*. It does not mean the
current principal or agent may use it. That still requires identity,
authorization, policy, and context. This mirrors the risk rule: registry state
is one input to the execution decision, never a substitute for it.

Versions are immutable. Changing anything execution- or governance-relevant
creates a new version rather than mutating the active definition, so a historical
audit record can always resolve the exact definition that ran.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol


def _now() -> datetime:
    return datetime.now(UTC)


class RegistryError(RuntimeError):
    """Raised when a lifecycle operation is not permitted."""


class RegistryDenied(RegistryError):
    """Raised when governance refuses a proposed lifecycle mutation.

    Distinct from RegistryError: the operation was structurally valid but
    policy, approval, or separation of duties refused it.
    """

    def __init__(self, verdict: MutationVerdict) -> None:
        super().__init__(verdict.detail or "registry mutation denied")
        self.verdict = verdict


class RegistryType(str, Enum):
    TOOL = "tool"
    AGENT = "agent"
    MODEL = "model"
    WORKFLOW = "workflow"


class ResourceStatus(str, Enum):
    """Lifecycle state. Deliberately richer than a boolean.

    ``enabled`` is not the lifecycle model; it is a *computed* execution
    property derived from this enum — see ``ExecutionAvailability``.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"
    SUSPENDED = "suspended"


class ExecutionAvailability(str, Enum):
    AVAILABLE = "available"
    POLICY_GATED = "policy_gated"
    UNAVAILABLE = "unavailable"


_AVAILABILITY: dict[ResourceStatus, ExecutionAvailability] = {
    ResourceStatus.ACTIVE: ExecutionAvailability.AVAILABLE,
    ResourceStatus.DRAFT: ExecutionAvailability.UNAVAILABLE,
    ResourceStatus.DEPRECATED: ExecutionAvailability.POLICY_GATED,
    ResourceStatus.RETIRED: ExecutionAvailability.UNAVAILABLE,
    ResourceStatus.SUSPENDED: ExecutionAvailability.UNAVAILABLE,
}


class OwnerType(str, Enum):
    USER = "user"
    GROUP = "group"
    SERVICE = "service"


class RegistryOperation(str, Enum):
    REGISTER = "register"
    CREATE_VERSION = "create_version"
    VALIDATE = "validate"
    PROMOTE = "promote"
    ACTIVATE = "activate"
    DEPRECATE = "deprecate"
    RETIRE = "retire"
    SUSPEND = "suspend"
    RESUME = "resume"
    TRANSFER_OWNERSHIP = "transfer_ownership"


class VersionBump(str, Enum):
    PATCH = "patch"  # metadata / documentation / non-behavioural
    MINOR = "minor"  # backward-compatible capability or contract extension
    MAJOR = "major"  # breaking behaviour, authz, schema, execution, governance


_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, raw: str | Version) -> Version:
        if isinstance(raw, Version):
            return raw
        match = _SEMVER.match(str(raw).strip())
        if not match:
            raise RegistryError(f"version must be MAJOR.MINOR.PATCH, got {raw!r}")
        return cls(*(int(g) for g in match.groups()))

    def bump(self, kind: VersionBump) -> Version:
        if kind is VersionBump.MAJOR:
            return Version(self.major + 1, 0, 0)
        if kind is VersionBump.MINOR:
            return Version(self.major, self.minor + 1, 0)
        return Version(self.major, self.minor, self.patch + 1)


@dataclass(frozen=True)
class Owner:
    owner_type: OwnerType
    owner_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"owner_type": self.owner_type.value, "owner_id": self.owner_id}

    @classmethod
    def from_dict(cls, raw: Any) -> Owner:
        if isinstance(raw, str):  # legacy: a bare owner name
            return cls(owner_type=OwnerType.GROUP, owner_id=raw)
        if not raw:
            return cls(owner_type=OwnerType.SERVICE, owner_id="unassigned")
        return cls(
            owner_type=OwnerType(raw.get("owner_type", "group")),
            owner_id=raw.get("owner_id", "unassigned"),
        )


@dataclass(frozen=True)
class PromotionRecord:
    resource_id: str
    version: Version
    source_environment: str
    target_environment: str
    requested_by: str
    approved_by: str | None
    timestamp: datetime
    trace_id: str
    validation_results: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "version": str(self.version),
            "source_environment": self.source_environment,
            "target_environment": self.target_environment,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id,
            "validation_results": list(self.validation_results),
        }


@dataclass(frozen=True)
class RegistryEvent:
    """Audit record for one lifecycle mutation."""

    registry_type: RegistryType
    resource_id: str
    version: Version
    operation: RegistryOperation
    principal: str
    previous_state: ResourceStatus | None = None
    new_state: ResourceStatus | None = None
    reason: str = ""
    approval_reference: str | None = None
    environment: str | None = None
    trace_id: str = ""
    registry_event_id: str = field(
        default_factory=lambda: f"reg-{uuid.uuid4().hex[:12]}"
    )
    timestamp: datetime = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_event_id": self.registry_event_id,
            "trace_id": self.trace_id,
            "registry_type": self.registry_type.value,
            "resource_id": self.resource_id,
            "version": str(self.version),
            "operation": self.operation.value,
            "previous_state": (
                self.previous_state.value if self.previous_state else None
            ),
            "new_state": self.new_state.value if self.new_state else None,
            "principal": self.principal,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "approval_reference": self.approval_reference,
            "environment": self.environment,
        }


@dataclass(frozen=True)
class MutationContext:
    """A proposed lifecycle mutation, as governance sees it.

    Carries everything §3B requires: authoritative principal identity, target
    environment, reason, trace ID, and approval state. Registry state
    transitions happen only after a governor returns an allowing verdict.
    """

    registry_type: RegistryType
    resource_id: str
    version: Version
    operation: RegistryOperation
    principal: str
    principal_roles: frozenset[str] = frozenset()
    principal_groups: frozenset[str] = frozenset()
    target_environment: str | None = None
    resource_environments: frozenset[str] = frozenset()
    resource_status: ResourceStatus | None = None
    resource_risk_level: str | None = None
    resource_risk_domains: frozenset[str] = frozenset()
    reason: str = ""
    trace_id: str = ""
    approver: str | None = None
    approval_granted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_type": self.registry_type.value,
            "resource_id": self.resource_id,
            "version": str(self.version),
            "operation": self.operation.value,
            "principal": self.principal,
            "target_environment": self.target_environment,
            "reason": self.reason,
            "trace_id": self.trace_id,
            "approver": self.approver,
            "approval_granted": self.approval_granted,
        }


@dataclass(frozen=True)
class MutationVerdict:
    """Governance's answer. Only `allowed` may permit a state transition."""

    allowed: bool
    decision: str = "allow"
    matched: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "governance_allowed": self.allowed,
            "governance_decision": self.decision,
            "governance_matched": list(self.matched),
            "governance_reason_codes": list(self.reason_codes),
        }


class MutationGovernor(Protocol):
    """Decides whether a proposed registry mutation may proceed.

    Implemented in `registry_governance`, which owns the policy dependency.
    Keeping it a protocol here means the registry never imports the policy
    engine, so the two remain independently testable.
    """

    def evaluate(self, context: MutationContext) -> MutationVerdict: ...


@dataclass(frozen=True)
class GovernedResourceMetadata:
    """Governance identity shared by every registry resource.

    Composed into resource definitions rather than inherited, so the
    resource-specific schema stays its own type.
    """

    registry_type: RegistryType
    resource_id: str
    version: Version
    display_name: str = ""
    description: str = ""
    owner: Owner = field(
        default_factory=lambda: Owner(OwnerType.SERVICE, "unassigned")
    )
    status: ResourceStatus = ResourceStatus.DRAFT
    created_at: datetime = field(default_factory=_now)
    created_by: str = "system"
    updated_at: datetime = field(default_factory=_now)
    updated_by: str = "system"
    metadata: dict[str, Any] = field(default_factory=dict)

    # Environments this version has been promoted into.
    environments: frozenset[str] = frozenset()

    # Deprecation bookkeeping.
    deprecated_at: datetime | None = None
    retirement_target_date: datetime | None = None
    replacement_resource_id: str | None = None
    replacement_version: str | None = None

    @property
    def availability(self) -> ExecutionAvailability:
        return _AVAILABILITY[self.status]

    @property
    def enabled(self) -> bool:
        """Computed execution property. Never the lifecycle model itself."""
        return self.availability is not ExecutionAvailability.UNAVAILABLE

    @property
    def key(self) -> tuple[str, str]:
        return (self.resource_id, str(self.version))

    def in_environment(self, environment: str | None) -> bool:
        if environment is None:
            return True
        return environment in self.environments

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_type": self.registry_type.value,
            "resource_id": self.resource_id,
            "version": str(self.version),
            "display_name": self.display_name,
            "description": self.description,
            "owner": self.owner.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "updated_at": self.updated_at.isoformat(),
            "updated_by": self.updated_by,
            "environments": sorted(self.environments),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(
        cls, raw: dict[str, Any], registry_type: RegistryType, resource_id: str
    ) -> GovernedResourceMetadata:
        """Parse metadata, migrating the legacy ``enabled`` boolean.

        A legacy document with ``enabled: true`` becomes ACTIVE and ``enabled:
        false`` becomes RETIRED — the closest honest reading, since the old
        format could not distinguish a retired tool from a suspended one.
        """
        if "status" in raw:
            status = ResourceStatus(raw["status"])
        elif "enabled" in raw:
            status = (
                ResourceStatus.ACTIVE if raw["enabled"] else ResourceStatus.RETIRED
            )
        else:
            status = ResourceStatus.DRAFT

        environments = raw.get("environments")
        if environments is None:
            constraints = raw.get("constraints", {}) or {}
            declared = constraints.get("environments", ("*",))
            environments = (
                ("dev", "test", "prod") if "*" in declared else tuple(declared)
            )

        return cls(
            registry_type=registry_type,
            resource_id=resource_id,
            version=Version.parse(raw.get("version", "1.0.0")),
            display_name=raw.get("display_name", resource_id),
            description=raw.get("description", ""),
            owner=Owner.from_dict(raw.get("owner")),
            status=status,
            created_by=raw.get("created_by", "system"),
            updated_by=raw.get("updated_by", "system"),
            metadata=raw.get("metadata", {}) or {},
            environments=frozenset(environments),
            replacement_resource_id=raw.get("replacement_resource_id"),
            replacement_version=raw.get("replacement_version"),
        )


class GovernedResource:
    """Anything the RegistryService manages. Composition, not inheritance."""

    metadata: GovernedResourceMetadata

    def with_metadata(self, metadata: GovernedResourceMetadata) -> GovernedResource:
        raise NotImplementedError


Validator = Callable[["GovernedResource"], list[str]]


class _ApprovalShim:
    """Adapts a bare approver string to the approval shape governance reads.

    Lets existing call sites keep passing ``approved_by="boss"`` while the
    governor sees a granted approval by that principal.
    """

    __slots__ = ("approver", "state")

    def __init__(self, approver: str | None) -> None:
        self.approver = approver
        self.state = _GRANTED if approver else None


class _GrantedState:
    value = "approved"


_GRANTED = _GrantedState()


class RegistryService:
    """Common lifecycle for every governed registry.

    There is deliberately no ``execute()``. Execution is resource-specific and
    governed elsewhere — the Execution Gateway for tools. A registry decides
    what a resource *is*, never whether a caller may use it.

    Every mutation returns a new object and emits a RegistryEvent. Stored
    definitions are frozen dataclasses, so a caller cannot reach in and change
    an active definition without going through a lifecycle operation.
    """

    def __init__(
        self,
        registry_type: RegistryType,
        validators: tuple[Validator, ...] = (),
        governor: MutationGovernor | None = None,
    ) -> None:
        self.registry_type = registry_type
        self._validators = validators
        self._governor = governor
        self._resources: dict[tuple[str, str], GovernedResource] = {}
        self._retired_ids: set[str] = set()
        self._events: list[RegistryEvent] = []
        self._promotions: list[PromotionRecord] = []

    # -- governance ----------------------------------------------------------

    def _govern(
        self,
        resource: GovernedResource,
        operation: RegistryOperation,
        principal: str,
        *,
        principal_roles: frozenset[str] = frozenset(),
        principal_groups: frozenset[str] = frozenset(),
        target_environment: str | None = None,
        reason: str = "",
        trace_id: str = "",
        approval: Any | None = None,
    ) -> MutationVerdict:
        """Evaluate a proposed mutation. Raises RegistryDenied if refused.

        With no governor configured the mutation is allowed and recorded as
        ungoverned, so the audit trail distinguishes "policy permitted this"
        from "nothing evaluated it".
        """
        if self._governor is None:
            return MutationVerdict(allowed=True, decision="ungoverned")

        meta = resource.metadata
        spec = getattr(resource, "spec", None)
        risk_level = getattr(getattr(spec, "risk_level", None), "value", None)
        risk_domains = frozenset(
            d.value for d in getattr(spec, "risk_domains", frozenset())
        )

        approver = getattr(approval, "approver", None)
        state = getattr(approval, "state", None)
        granted = getattr(state, "value", None) == "approved"

        verdict = self._governor.evaluate(
            MutationContext(
                registry_type=meta.registry_type,
                resource_id=meta.resource_id,
                version=meta.version,
                operation=operation,
                principal=principal,
                principal_roles=principal_roles,
                principal_groups=principal_groups,
                target_environment=target_environment,
                resource_environments=meta.environments,
                resource_status=meta.status,
                resource_risk_level=risk_level,
                resource_risk_domains=risk_domains,
                reason=reason,
                trace_id=trace_id,
                approver=approver,
                approval_granted=granted,
            )
        )
        if not verdict.allowed:
            # Refusals are audited too — a denied mutation must not be silent.
            self._events.append(
                RegistryEvent(
                    registry_type=meta.registry_type,
                    resource_id=meta.resource_id,
                    version=meta.version,
                    operation=operation,
                    principal=principal,
                    previous_state=meta.status,
                    new_state=meta.status,
                    reason=verdict.detail,
                    environment=target_environment,
                    trace_id=trace_id,
                )
            )
            raise RegistryDenied(verdict)
        return verdict

    # -- audit ---------------------------------------------------------------

    @property
    def events(self) -> tuple[RegistryEvent, ...]:
        return tuple(self._events)

    @property
    def promotions(self) -> tuple[PromotionRecord, ...]:
        return tuple(self._promotions)

    def events_for(self, resource_id: str) -> tuple[RegistryEvent, ...]:
        return tuple(e for e in self._events if e.resource_id == resource_id)

    def _emit(
        self,
        resource: GovernedResource,
        operation: RegistryOperation,
        principal: str,
        previous: ResourceStatus | None = None,
        **extra: Any,
    ) -> RegistryEvent:
        meta = resource.metadata
        event = RegistryEvent(
            registry_type=meta.registry_type,
            resource_id=meta.resource_id,
            version=meta.version,
            operation=operation,
            principal=principal,
            previous_state=previous,
            new_state=meta.status,
            **extra,
        )
        self._events.append(event)
        return event

    # -- read ----------------------------------------------------------------

    def get(
        self, resource_id: str, version: str | Version | None = None
    ) -> GovernedResource | None:
        """Fetch a specific version, or the newest if unspecified.

        Retired resources remain retrievable: historical audit must be able to
        resolve the exact definition that ran.
        """
        if version is not None:
            return self._resources.get((resource_id, str(Version.parse(version))))
        versions = self.versions_of(resource_id)
        return versions[-1] if versions else None

    def get_active(
        self, resource_id: str, environment: str | None = None
    ) -> GovernedResource | None:
        """The version that is operationally available in an environment."""
        candidates = [
            r
            for r in self.versions_of(resource_id)
            if r.metadata.status
            in (ResourceStatus.ACTIVE, ResourceStatus.DEPRECATED)
            and r.metadata.in_environment(environment)
        ]
        return candidates[-1] if candidates else None

    def versions_of(self, resource_id: str) -> tuple[GovernedResource, ...]:
        return tuple(
            sorted(
                (
                    r
                    for (rid, _), r in self._resources.items()
                    if rid == resource_id
                ),
                key=lambda r: r.metadata.version,
            )
        )

    def list(
        self,
        *,
        status: ResourceStatus | None = None,
        environment: str | None = None,
    ) -> tuple[GovernedResource, ...]:
        results = tuple(self._resources.values())
        if status is not None:
            results = tuple(r for r in results if r.metadata.status is status)
        if environment is not None:
            results = tuple(
                r for r in results if r.metadata.in_environment(environment)
            )
        return results

    def is_retired_id(self, resource_id: str) -> bool:
        return resource_id in self._retired_ids

    def __len__(self) -> int:
        return len(self._resources)

    def __iter__(self) -> Iterator[GovernedResource]:
        return iter(self._resources.values())

    # -- lifecycle -----------------------------------------------------------

    def register(
        self,
        resource: GovernedResource,
        *,
        principal: str = "system",
        principal_roles: frozenset[str] = frozenset(),
        reason: str = "",
        trace_id: str = "",
        approval: Any | None = None,
    ) -> GovernedResource:
        meta = resource.metadata
        if meta.registry_type is not self.registry_type:
            raise RegistryError(
                f"{meta.registry_type.value} resource cannot be registered in a "
                f"{self.registry_type.value} registry"
            )
        if meta.resource_id in self._retired_ids:
            raise RegistryError(
                f"resource_id '{meta.resource_id}' is retired and cannot be reused"
            )
        if meta.key in self._resources:
            raise RegistryError(
                f"'{meta.resource_id}' version {meta.version} is already registered; "
                "versions are immutable — use create_version()"
            )

        self._govern(
            resource,
            RegistryOperation.REGISTER,
            principal,
            principal_roles=principal_roles,
            reason=reason,
            trace_id=trace_id,
            approval=approval,
        )
        self._resources[meta.key] = resource
        self._emit(resource, RegistryOperation.REGISTER, principal, trace_id=trace_id)
        return resource

    def create_version(
        self,
        resource_id: str,
        bump: VersionBump,
        *,
        spec_changes: dict[str, Any] | None = None,
        principal: str = "system",
        principal_roles: frozenset[str] = frozenset(),
        reason: str = "",
        trace_id: str = "",
        approval: Any | None = None,
    ) -> GovernedResource:
        """Create a new immutable version. The prior version is untouched."""
        current = self.get(resource_id)
        if current is None:
            raise RegistryError(f"unknown resource: {resource_id}")

        self._govern(
            current,
            RegistryOperation.CREATE_VERSION,
            principal,
            principal_roles=principal_roles,
            reason=reason,
            trace_id=trace_id,
            approval=approval,
        )

        new_version = current.metadata.version.bump(bump)
        new_meta = replace(
            current.metadata,
            version=new_version,
            status=ResourceStatus.DRAFT,
            environments=frozenset(),
            updated_at=_now(),
            updated_by=principal,
        )
        updated = current.with_metadata(new_meta)
        if spec_changes:
            updated = updated.with_spec_changes(spec_changes)  # type: ignore[attr-defined]

        self._resources[new_meta.key] = updated
        self._emit(
            updated,
            RegistryOperation.CREATE_VERSION,
            principal,
            reason=reason,
            trace_id=trace_id,
        )
        return updated

    def validate(self, resource_id: str, version: str | Version) -> list[str]:
        resource = self.get(resource_id, version)
        if resource is None:
            raise RegistryError(f"unknown resource: {resource_id} {version}")
        problems: list[str] = []
        for validator in self._validators:
            problems.extend(validator(resource))
        return problems

    def promote(
        self,
        resource_id: str,
        version: str | Version,
        *,
        source_environment: str,
        target_environment: str,
        requested_by: str,
        approved_by: str | None = None,
        trace_id: str = "",
        activate: bool = True,
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        reason: str = "",
    ) -> GovernedResource:
        """Promote a version into an environment.

        Promotion never mutates the version being promoted from — it records
        that the same immutable version is now available in a further
        environment.
        """
        resource = self.get(resource_id, version)
        if resource is None:
            raise RegistryError(f"unknown resource: {resource_id} {version}")
        if resource.metadata.status is ResourceStatus.RETIRED:
            raise RegistryError(f"cannot promote retired resource '{resource_id}'")

        self._govern(
            resource,
            RegistryOperation.PROMOTE,
            requested_by,
            principal_roles=principal_roles,
            target_environment=target_environment,
            reason=reason,
            trace_id=trace_id,
            approval=approval or _ApprovalShim(approved_by),
        )

        problems = self.validate(resource_id, version)
        if problems:
            raise RegistryError(
                f"validation failed for {resource_id} {version}: {'; '.join(problems)}"
            )

        previous = resource.metadata.status
        new_status = (
            ResourceStatus.ACTIVE
            if activate and previous is ResourceStatus.DRAFT
            else previous
        )
        new_meta = replace(
            resource.metadata,
            status=new_status,
            environments=resource.metadata.environments | {target_environment},
            updated_at=_now(),
            updated_by=requested_by,
        )
        promoted = resource.with_metadata(new_meta)
        self._resources[new_meta.key] = promoted

        record = PromotionRecord(
            resource_id=resource_id,
            version=new_meta.version,
            source_environment=source_environment,
            target_environment=target_environment,
            requested_by=requested_by,
            approved_by=approved_by,
            timestamp=_now(),
            trace_id=trace_id,
            validation_results=("ok",),
        )
        self._promotions.append(record)
        self._emit(
            promoted,
            RegistryOperation.PROMOTE,
            requested_by,
            previous,
            environment=target_environment,
            approval_reference=approved_by,
            trace_id=trace_id,
        )
        return promoted

    def _transition(
        self,
        resource_id: str,
        version: str | Version | None,
        new_status: ResourceStatus,
        operation: RegistryOperation,
        principal: str,
        *,
        allowed_from: tuple[ResourceStatus, ...] | None = None,
        reason: str = "",
        approval_reference: str | None = None,
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        trace_id: str = "",
        **meta_changes: Any,
    ) -> GovernedResource:
        resource = self.get(resource_id, version)
        if resource is None:
            raise RegistryError(f"unknown resource: {resource_id}")

        previous = resource.metadata.status
        if allowed_from is not None and previous not in allowed_from:
            raise RegistryError(
                f"cannot {operation.value} '{resource_id}' from state "
                f"{previous.value}; allowed from "
                f"{', '.join(s.value for s in allowed_from)}"
            )

        self._govern(
            resource,
            operation,
            principal,
            principal_roles=principal_roles,
            reason=reason,
            trace_id=trace_id,
            approval=approval or _ApprovalShim(approval_reference),
        )

        new_meta = replace(
            resource.metadata,
            status=new_status,
            updated_at=_now(),
            updated_by=principal,
            **meta_changes,
        )
        updated = resource.with_metadata(new_meta)
        self._resources[new_meta.key] = updated
        self._emit(
            updated,
            operation,
            principal,
            previous,
            reason=reason,
            approval_reference=approval_reference,
        )
        return updated

    def activate(
        self,
        resource_id: str,
        version: str | Version | None = None,
        *,
        principal: str = "system",
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        trace_id: str = "",
    ) -> GovernedResource:
        return self._transition(
            resource_id,
            version,
            ResourceStatus.ACTIVE,
            RegistryOperation.ACTIVATE,
            principal,
            principal_roles=principal_roles,
            approval=approval,
            trace_id=trace_id,
            allowed_from=(ResourceStatus.DRAFT, ResourceStatus.SUSPENDED),
        )

    def deprecate(
        self,
        resource_id: str,
        version: str | Version | None = None,
        *,
        principal: str = "system",
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        trace_id: str = "",
        retirement_target_date: datetime | None = None,
        replacement_resource_id: str | None = None,
        replacement_version: str | None = None,
        reason: str = "",
    ) -> GovernedResource:
        return self._transition(
            resource_id,
            version,
            ResourceStatus.DEPRECATED,
            RegistryOperation.DEPRECATE,
            principal,
            principal_roles=principal_roles,
            approval=approval,
            trace_id=trace_id,
            allowed_from=(ResourceStatus.ACTIVE,),
            reason=reason,
            deprecated_at=_now(),
            retirement_target_date=retirement_target_date,
            replacement_resource_id=replacement_resource_id,
            replacement_version=replacement_version,
        )

    def retire(
        self,
        resource_id: str,
        version: str | Version | None = None,
        *,
        principal: str = "system",
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        trace_id: str = "",
        reason: str = "",
        approval_reference: str | None = None,
    ) -> GovernedResource:
        """Retire a resource. The identifier is burned, never silently reused."""
        retired = self._transition(
            resource_id,
            version,
            ResourceStatus.RETIRED,
            RegistryOperation.RETIRE,
            principal,
            principal_roles=principal_roles,
            approval=approval,
            trace_id=trace_id,
            reason=reason,
            approval_reference=approval_reference,
        )
        self._retired_ids.add(resource_id)
        return retired

    def suspend(
        self,
        resource_id: str,
        version: str | Version | None = None,
        *,
        principal: str = "system",
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        trace_id: str = "",
        reason: str = "",
    ) -> GovernedResource:
        """Emergency operational control. Immediate, reversible, audited.

        No version change, no schema change. One of the mechanisms the future
        kill-switch system will use.
        """
        return self._transition(
            resource_id,
            version,
            ResourceStatus.SUSPENDED,
            RegistryOperation.SUSPEND,
            principal,
            principal_roles=principal_roles,
            approval=approval,
            trace_id=trace_id,
            allowed_from=(ResourceStatus.ACTIVE, ResourceStatus.DEPRECATED),
            reason=reason,
        )

    def resume(
        self,
        resource_id: str,
        version: str | Version | None = None,
        *,
        principal: str = "system",
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        trace_id: str = "",
        reason: str = "",
    ) -> GovernedResource:
        return self._transition(
            resource_id,
            version,
            ResourceStatus.ACTIVE,
            RegistryOperation.RESUME,
            principal,
            principal_roles=principal_roles,
            approval=approval,
            trace_id=trace_id,
            allowed_from=(ResourceStatus.SUSPENDED,),
            reason=reason,
        )

    def transfer_ownership(
        self,
        resource_id: str,
        new_owner: Owner,
        *,
        version: str | Version | None = None,
        principal: str = "system",
        approval_reference: str | None = None,
        reason: str = "",
        principal_roles: frozenset[str] = frozenset(),
        approval: Any | None = None,
        trace_id: str = "",
    ) -> GovernedResource:
        resource = self.get(resource_id, version)
        if resource is None:
            raise RegistryError(f"unknown resource: {resource_id}")

        self._govern(
            resource,
            RegistryOperation.TRANSFER_OWNERSHIP,
            principal,
            principal_roles=principal_roles,
            reason=reason,
            trace_id=trace_id,
            approval=approval or _ApprovalShim(approval_reference),
        )

        new_meta = replace(
            resource.metadata,
            owner=new_owner,
            updated_at=_now(),
            updated_by=principal,
        )
        updated = resource.with_metadata(new_meta)
        self._resources[new_meta.key] = updated
        self._emit(
            updated,
            RegistryOperation.TRANSFER_OWNERSHIP,
            principal,
            resource.metadata.status,
            reason=reason or f"owner -> {new_owner.owner_id}",
            approval_reference=approval_reference,
        )
        return updated

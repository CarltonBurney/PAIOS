#!/usr/bin/env python3
"""Structural validation for the Tier-1 remediation Logic Apps definition.

Checks that the workflow is internally consistent and free of leaked secrets
before it reaches a reviewer or a deployment. Exits non-zero on any failure.

Usage:
    python3 tier1-remediation/scripts/validate_workflow.py [path/to/workflow.json]
"""

import json
import re
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "workflows" / "tier1-remediation.json"

# Placeholder identifiers the reference implementation is expected to ship with.
# Anything outside these is treated as a real tenant value that must not be committed.
ALLOWED_GUIDS = {
    # All-zero placeholder used for subscription, team, and channel identifiers.
    "00000000-0000-0000-0000-000000000000",
    # Well-known Microsoft Graph identifier for a user's password authentication
    # method. Documented and constant across every tenant, not a secret.
    "28c10230-6103-485e-b985-444c60001490",
}

GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")

# Azure Automation webhook URIs carry a bearer token in the query string.
WEBHOOK_RE = re.compile(
    r"https://[a-z0-9-]+\.webhook\.[a-z0-9-]+\.azure-automation\.net", re.IGNORECASE
)

failures: list[str] = []
checks_run = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks_run
    checks_run += 1
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
        failures.append(name)


def collect_actions(actions: dict, path: str = "") -> list[tuple[str, dict, str]]:
    """Flatten every action in the tree, including nested scopes, conditions,
    switch cases, and until loops. Returns (name, body, parent_path) triples."""
    found = []
    for name, body in actions.items():
        found.append((name, body, path))
        here = f"{path}/{name}"
        if isinstance(body.get("actions"), dict):
            found += collect_actions(body["actions"], here)
        els = body.get("else")
        if isinstance(els, dict) and isinstance(els.get("actions"), dict):
            found += collect_actions(els["actions"], f"{here}[else]")
        for case_name, case_body in (body.get("cases") or {}).items():
            if isinstance(case_body.get("actions"), dict):
                found += collect_actions(case_body["actions"], f"{here}[{case_name}]")
        default = body.get("default")
        if isinstance(default, dict) and isinstance(default.get("actions"), dict):
            found += collect_actions(default["actions"], f"{here}[default]")
    return found


def check_run_after(actions: dict, path: str = "") -> list[str]:
    """Every runAfter dependency must name a sibling in the same action group."""
    problems = []
    siblings = set(actions)
    for name, body in actions.items():
        for dep in (body.get("runAfter") or {}):
            if dep not in siblings:
                problems.append(f"{path}/{name}: runAfter '{dep}' is not a sibling")
        here = f"{path}/{name}"
        if isinstance(body.get("actions"), dict):
            problems += check_run_after(body["actions"], here)
        els = body.get("else")
        if isinstance(els, dict) and isinstance(els.get("actions"), dict):
            problems += check_run_after(els["actions"], f"{here}[else]")
        for case_name, case_body in (body.get("cases") or {}).items():
            if isinstance(case_body.get("actions"), dict):
                problems += check_run_after(case_body["actions"], f"{here}[{case_name}]")
        default = body.get("default")
        if isinstance(default, dict) and isinstance(default.get("actions"), dict):
            problems += check_run_after(default["actions"], f"{here}[default]")
    return problems


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    print(f"Validating {path}\n")

    if not path.is_file():
        print(f"  FAIL  file not found: {path}")
        return 1

    raw = path.read_text(encoding="utf-8")

    # 1. Parses as JSON.
    try:
        doc = json.loads(raw)
        check("workflow parses as JSON", True)
    except json.JSONDecodeError as exc:
        check("workflow parses as JSON", False, str(exc))
        return 1

    definition = doc.get("definition")
    if not isinstance(definition, dict):
        check("top-level 'definition' object present", False)
        return 1
    check("top-level 'definition' object present", True)

    # 2. Declares the Logic Apps workflow definition schema.
    check(
        "declares workflowdefinition schema",
        "workflowdefinition.json" in definition.get("$schema", ""),
        f"got {definition.get('$schema')!r}",
    )

    # 3. Exactly one trigger, and it is an HTTP request trigger.
    triggers = definition.get("triggers") or {}
    check("exactly one trigger defined", len(triggers) == 1, f"found {len(triggers)}")
    if triggers:
        trigger = next(iter(triggers.values()))
        check(
            "trigger is an HTTP Request trigger",
            trigger.get("type") == "Request" and trigger.get("kind") == "Http",
            f"type={trigger.get('type')} kind={trigger.get('kind')}",
        )

    actions = definition.get("actions") or {}
    all_actions = collect_actions(actions)
    names = [n for n, _, _ in all_actions]
    print(f"\n  ...{len(names)} actions discovered\n")

    # 4. Action names are unique across the whole tree. Logic Apps references
    #    actions by bare name, so a duplicate makes body()/outputs() ambiguous.
    duplicates = sorted({n for n in names if names.count(n) > 1})
    check("action names are unique", not duplicates, f"duplicates: {duplicates}")

    # 5. Every runAfter target exists as a sibling.
    ra_problems = check_run_after(actions)
    check("runAfter targets resolve", not ra_problems, "; ".join(ra_problems))

    # 6. Every body()/outputs() reference names a real action.
    serialized = json.dumps(definition)
    refs = set(re.findall(r"(?:body|outputs)\('([^']+)'\)", serialized))
    dangling = sorted(r for r in refs if r not in names)
    check("body()/outputs() references resolve", not dangling, f"dangling: {dangling}")

    # 7. Variables are initialized before use.
    initialized = set()
    for name, body, _ in all_actions:
        if body.get("type") == "InitializeVariable":
            for var in body.get("inputs", {}).get("variables", []):
                initialized.add(var["name"])
    referenced = set(re.findall(r"variables\('([^']+)'\)", serialized))
    uninitialized = sorted(referenced - initialized)
    check(
        "all referenced variables are initialized",
        not uninitialized,
        f"missing: {uninitialized}",
    )

    # 8. Parameters are declared before use.
    declared_params = set(definition.get("parameters") or {})
    referenced_params = set(re.findall(r"parameters\('([^']+)'\)", serialized))
    undeclared = sorted(referenced_params - declared_params)
    check(
        "all referenced parameters are declared",
        not undeclared,
        f"missing: {undeclared}",
    )

    # 9. No real tenant GUIDs. This is the check that catches a genuine mistake
    #    later — someone pasting a live subscription or team id into the sample.
    found_guids = set(GUID_RE.findall(raw))
    leaked = sorted(g for g in found_guids if g.lower() not in ALLOWED_GUIDS)
    check("no non-placeholder GUIDs committed", not leaked, f"found: {leaked}")

    # 10. No Azure Automation webhook URI. These embed a bearer token — anyone
    #     holding one can start the runbook.
    webhooks = WEBHOOK_RE.findall(raw)
    check("no Automation webhook URI committed", not webhooks, f"found: {webhooks}")

    # 11. The runbook webhook parameter stays a SecureString with an empty default.
    runbook = (definition.get("parameters") or {}).get("printerRunbookWebhookUri", {})
    check(
        "printerRunbookWebhookUri is SecureString with empty default",
        runbook.get("type") == "SecureString" and runbook.get("defaultValue") == "",
        f"type={runbook.get('type')} default={runbook.get('defaultValue')!r}",
    )

    # 12. Credential-bearing actions suppress their payloads from run history.
    #     Without this, temporary passwords are readable by anyone with reader
    #     access to the Logic App's runs.
    reset_action = next(
        (b for n, b, _ in all_actions if n == "Request_Password_Reset"), None
    )
    if reset_action is None:
        check("password reset action present", False)
    else:
        secure = (
            reset_action.get("runtimeConfiguration", {})
            .get("secureData", {})
            .get("properties", [])
        )
        check(
            "password reset marks inputs and outputs as secure",
            "inputs" in secure and "outputs" in secure,
            f"secureData.properties={secure}",
        )

    print(f"\n{checks_run - len(failures)}/{checks_run} checks passed")
    if failures:
        print("\nFailed checks:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("Workflow definition is structurally valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

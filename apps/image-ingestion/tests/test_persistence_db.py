"""PostgreSQL coordinator tests (fixture database only; no cloud, no live writes).

Requires PAIOS_TEST_DATABASE_URL: an admin connection string to a disposable
PostgreSQL server (CI uses a service container). A fresh database is created per
module; repositories run as least-privilege roles so RLS and grants are real.
"""
import hashlib
import os
import threading
import time
import uuid
from importlib import resources

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402

from conftest import error_of  # noqa: E402
from persistence_helpers import TestPublisher, chain, deep, marker, payloads  # noqa: E402
from paios_ingestion import contract  # noqa: E402
from paios_ingestion.persistence import repository as repository_module  # noqa: E402
from paios_ingestion.persistence.canonical import request_digest, sha256_hex  # noqa: E402
from paios_ingestion.persistence.fakes import FakeDrive  # noqa: E402
from paios_ingestion.persistence.migrate import apply_migrations, grants_sql  # noqa: E402
from paios_ingestion.persistence.repository import PAYLOAD_ROLES, CoordinatorRepository  # noqa: E402

ADMIN = os.environ.get("PAIOS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN, reason="PAIOS_TEST_DATABASE_URL not set")
SECRET = b"s" * 32
FINGERPRINT = "f" * 64


@pytest.fixture(scope="module")
def urls():
    name = f"paios_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
        for role, bypass in (("paios_app", "NOBYPASSRLS"), ("paios_ops", "BYPASSRLS")):
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(f"{verb} ROLE {role} LOGIN NOSUPERUSER {bypass} PASSWORD '{role}'")
    admin = make_conninfo(ADMIN, dbname=name)
    with psycopg.connect(admin, autocommit=True) as conn:
        apply_migrations(conn)
        conn.execute(grants_sql())
        conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO paios_app, paios_ops')
    yield {"admin": admin,
           "app": make_conninfo(admin, user="paios_app", password="paios_app"),
           "ops": make_conninfo(admin, user="paios_ops", password="paios_ops")}
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def app(urls):
    return CoordinatorRepository(urls["app"], cursor_secret=SECRET)


@pytest.fixture
def ops(urls):
    return CoordinatorRepository(urls["ops"], cursor_secret=SECRET)


@pytest.fixture
def gen(ops):
    generation, mode = ops.publisher_generation()
    if mode == "recovery":
        ops.resume(generation)
    return generation


@pytest.fixture
def scope():
    return contract.Scope(f"tenant-{uuid.uuid4().hex[:8]}", "workspace-1")


@pytest.fixture
def publisher(app):
    return TestPublisher(app, FakeDrive())


def request(item="item-1", languages=("en",)):
    return {"schema_version": "1.0.0", "workspace_id": "workspace-1", "force_reprocess": False,
            "context_release_id": None,
            "source": {"connector": "onedrive", "root_id": "root", "item_id": item, "version": "1",
                       "original_filename": "a.png"},
            "ocr": {"enabled": True, "languages": list(languages), "provider_profile": "p"}}


def error(excinfo):
    return error_of(excinfo)


# -- migrations ---------------------------------------------------------------------

def test_migrations_are_idempotent_and_tamper_evident(urls):
    with psycopg.connect(urls["admin"], autocommit=True) as conn:
        assert apply_migrations(conn) == []
        conn.execute("UPDATE paios_ingest.schema_migrations SET sha256 = 'x' WHERE name = '0001_foundation.sql'")
        with pytest.raises(RuntimeError):
            apply_migrations(conn)
        original = resources.files("paios_ingestion.persistence.migrations").joinpath("0001_foundation.sql")
        conn.execute("UPDATE paios_ingest.schema_migrations SET sha256 = %s WHERE name = '0001_foundation.sql'",
                     (hashlib.sha256(original.read_bytes()).hexdigest(),))


# -- A02 reservations -------------------------------------------------------------------

def test_reservation_replay_and_conflict(app, gen, scope):
    body = request()
    digest = request_digest(scope, body)
    first = app.reserve(scope=scope, idempotency_key="k1", request_digest=digest, request=body,
                        pinned={"provider_profile_version": "3"})
    again = app.reserve(scope=scope, idempotency_key="k1", request_digest=digest, request=body)
    assert (again.ingestion_id, again.asset_id, again.replay) == (first.ingestion_id, first.asset_id, True)
    assert first.replay is False
    assert app.reservation_pins(scope, first.ingestion_id) == {"provider_profile_version": "3"}
    reordered = request(languages=("en",))
    assert request_digest(scope, reordered) == digest
    with pytest.raises(contract.PipelineFailure) as excinfo:
        other = request(item="item-2")
        app.reserve(scope=scope, idempotency_key="k1", request_digest=request_digest(scope, other), request=other)
    assert error(excinfo)["code"] == "IDEMPOTENCY_CONFLICT"
    elsewhere = contract.Scope(scope.tenant_id + "-b", scope.workspace_id)
    theirs = app.reserve(scope=elsewhere, idempotency_key="k1",
                         request_digest=request_digest(elsewhere, body), request=body)
    assert theirs.asset_id != first.asset_id  # keys are scoped


def test_concurrent_reservations_allocate_ids_once(app, gen, scope):
    body = request()
    digest = request_digest(scope, body)
    results = []

    def go():
        results.append(app.reserve(scope=scope, idempotency_key="race", request_digest=digest, request=body))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.asset_id for r in results}) == 1
    assert sum(not r.replay for r in results) == 1


# -- A01 publication chain, reads, jobs ---------------------------------------------------

def test_chain_publishes_and_reads_latest(app, gen, scope, publisher):
    bundles = chain(scope)
    receipts = [publisher.publish(scope, b, gen, FINGERPRINT) for b in bundles]
    asset = bundles[0]["registry"]["asset_id"]
    assert [r.registry_version for r in receipts] == [1, 2, 3]
    assert app.get(scope, asset) == bundles[2]["registry"]
    events = app.list_events(scope, asset, 0, 10)
    assert [e["event_type"] for e in events] == ["asset_accepted", "processing_started", "ingestion_completed"]
    job = app.get_job(scope, bundles[0]["registry"]["ingestion_id"])
    assert (job["status"], job["registry_version"]) == ("completed", 3)
    assert app.lookup_commit(scope, bundles[1]["commit_id"]) == receipts[1]
    assert app.find_reusable(scope, bundles[2]["registry"]["identity"]["sha256"], FINGERPRINT)["asset_id"] == asset


# -- A03 races and stale writers ----------------------------------------------------------

def test_one_commit_per_revision_slot(app, gen, scope, publisher):
    bundles = chain(scope)
    publisher.publish(scope, bundles[0], gen)
    rival = deep(bundles[1])
    rival["commit_id"] = str(uuid.uuid4())
    outcomes = []

    def freeze(bundle):
        try:
            publisher.freeze(scope, bundle, gen)
            outcomes.append("frozen")
        except contract.PipelineFailure as exc:
            outcomes.append(exc.error["code"])

    threads = [threading.Thread(target=freeze, args=(b,)) for b in (bundles[1], rival)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["VERSION_CONFLICT", "frozen"]


def test_identical_freeze_replays_and_changed_bytes_conflict(app, gen, scope, publisher):
    bundle = chain(scope)[0]
    ids = publisher.freeze(scope, bundle, gen)
    parts = payloads(bundle)
    mark = marker(scope, bundle, ids, parts, None)
    assert app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts, object_ids=ids,
                             marker=mark, generation=gen) == "frozen"
    with pytest.raises(contract.PipelineFailure) as excinfo:
        app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts, object_ids=ids,
                          marker=marker(scope, bundle, ids, parts, None, published_at="2026-09-23T07:00:00Z"),
                          generation=gen)
    assert error(excinfo)["code"] == "INTEGRITY_FAILED"


def test_stale_expected_version_is_rejected(app, gen, scope, publisher):
    bundles = chain(scope)
    publisher.publish(scope, bundles[0], gen)
    with pytest.raises(contract.PipelineFailure) as excinfo:
        publisher.freeze(scope, bundles[2], gen)
    assert error(excinfo)["code"] == "VERSION_CONFLICT"


# -- A21 / P2-A frozen intent integrity -----------------------------------------------------

def test_invalid_triple_is_rejected_before_freezing(app, gen, scope, publisher):
    bundle = deep(chain(scope)[0])
    bundle["index"]["status"] = "processing"
    with pytest.raises(contract.PipelineFailure) as excinfo:
        publisher.freeze(scope, bundle, gen)
    assert error(excinfo)["code"] == "INTEGRITY_FAILED"


@pytest.mark.parametrize("broken", ["previous", "file_sha", "object_id", "non_canonical"])
def test_marker_must_match_the_frozen_bundle(app, gen, scope, broken):
    bundle = chain(scope)[0]
    parts = payloads(bundle)
    ids = dict(zip(PAYLOAD_ROLES + ("commit.json",), FakeDrive().generate_ids(4)))
    mark = marker(scope, bundle, ids, parts, "a" * 64 if broken == "previous" else None)
    if broken == "file_sha":
        mark = mark.replace(sha256_hex(parts["index.json"]).encode(), b"0" * 64)
    if broken == "object_id":
        mark = mark.replace(ids["audit.json"].encode(), b"drive-other")
    if broken == "non_canonical":
        mark = mark.replace(b'","', b'", "', 1)
    with pytest.raises(contract.PipelineFailure) as excinfo:
        app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts, object_ids=ids,
                          marker=mark, generation=gen)
    assert error(excinfo)["code"] == "INTEGRITY_FAILED"


def test_non_canonical_payload_bytes_are_rejected(app, gen, scope):
    bundle = chain(scope)[0]
    parts = payloads(bundle)
    parts["index.json"] = parts["index.json"].replace(b":", b": ", 1)
    ids = dict(zip(PAYLOAD_ROLES + ("commit.json",), FakeDrive().generate_ids(4)))
    with pytest.raises(contract.PipelineFailure) as excinfo:
        app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts, object_ids=ids,
                          marker=b"{}", generation=gen)
    assert error(excinfo)["code"] == "INTEGRITY_FAILED"


# -- A04 crash injection in the visibility transaction ----------------------------------------

@pytest.mark.parametrize("point", ["record_published:before_head", "record_published:after_head",
                                   "record_published:after_revisions", "record_published:before_commit"])
def test_crash_inside_record_published_leaves_nothing_visible(urls, gen, scope, point):
    drive = FakeDrive()
    crashing = CoordinatorRepository(urls["app"], cursor_secret=SECRET,
                                     fault=lambda p: (_ for _ in ()).throw(RuntimeError("crash")) if p == point else None)
    healthy = CoordinatorRepository(urls["app"], cursor_secret=SECRET)
    bundle = chain(scope)[0]
    pub = TestPublisher(crashing, drive)
    pub.freeze(scope, bundle, gen)
    marker_id, marker_sha = pub.upload(scope, bundle["commit_id"], gen)
    with pytest.raises(RuntimeError):
        crashing.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
                                  marker_sha256=marker_sha, generation=gen)
    asset = bundle["registry"]["asset_id"]
    with pytest.raises(contract.PipelineFailure) as excinfo:
        healthy.get(scope, asset)
    assert error(excinfo)["code"] == "PERSISTENCE_UNAVAILABLE"  # marker may be out: not "not found"
    assert healthy.list_events(scope, asset, 0, 10) == []
    assert healthy.lookup_commit(scope, bundle["commit_id"]) is None
    receipt = healthy.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
                                       marker_sha256=marker_sha, generation=gen)  # recovery resumes
    assert receipt.registry_version == 1
    assert len(healthy.list_events(scope, asset, 0, 10)) == 1  # exactly one audit revision


# -- A05 lost response after commit ---------------------------------------------------------

def test_record_published_replay_returns_the_same_receipt(app, gen, scope, publisher):
    bundle = chain(scope)[0]
    publisher.freeze(scope, bundle, gen)
    marker_id, marker_sha = publisher.upload(scope, bundle["commit_id"], gen)
    first = app.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
                                 marker_sha256=marker_sha, generation=gen)
    again = app.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
                                 marker_sha256=marker_sha, generation=gen)
    assert again == first
    with pytest.raises(contract.PipelineFailure) as excinfo:
        app.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
                             marker_sha256="0" * 64, generation=gen)
    assert error(excinfo)["code"] == "INTEGRITY_FAILED"


def test_drive_lost_response_retries_with_the_same_ids(app, gen, scope):
    drive = FakeDrive()
    pub = TestPublisher(app, drive)
    drive.faults.inject("create", "lost_response", "fail_before")
    bundle = chain(scope)[0]
    pub.publish(scope, bundle, gen)
    frozen = app.frozen_intent(scope, bundle["commit_id"])
    assert sorted(drive.list_children(bundle["commit_id"]), key=lambda m: m["id"]) and \
        {m["id"] for m in drive.list_children(bundle["commit_id"])} == set(frozen["object_ids"].values())


def test_recording_requires_the_publishing_mark(app, gen, scope, publisher):
    bundle = chain(scope)[0]
    ids = publisher.freeze(scope, bundle, gen)
    frozen = app.frozen_intent(scope, bundle["commit_id"])
    with pytest.raises(contract.PipelineFailure) as excinfo:
        app.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=ids["commit.json"],
                             marker_sha256=sha256_hex(frozen["marker"]), generation=gen)
    assert error(excinfo)["code"] == "CONTRACT_MISMATCH"


# -- P2-C reads during uncertain publication ----------------------------------------------------

def test_get_refuses_a_possibly_stale_head_while_publishing(app, gen, scope, publisher):
    bundles = chain(scope)
    publisher.publish(scope, bundles[0], gen)
    asset = bundles[0]["registry"]["asset_id"]
    assert app.get(scope, asset)["status"] == "accepted"
    publisher.freeze(scope, bundles[1], gen)
    assert app.get(scope, asset)["status"] == "accepted"  # frozen: no marker can exist yet
    marker_id, marker_sha = publisher.upload(scope, bundles[1]["commit_id"], gen)  # marker is now out
    with pytest.raises(contract.PipelineFailure) as excinfo:
        app.get(scope, asset)
    err = error(excinfo)
    assert (err["code"], err["retryable"]) == ("PERSISTENCE_UNAVAILABLE", True)
    app.record_published(scope=scope, commit_id=bundles[1]["commit_id"], marker_object_id=marker_id,
                         marker_sha256=marker_sha, generation=gen)
    assert app.get(scope, asset)["status"] == "processing"


# -- P2-B recovery fences old publishers ---------------------------------------------------------

def test_recovery_fences_an_old_writer_and_the_new_generation_takes_over(app, ops, gen, scope):
    drive = FakeDrive()
    old = TestPublisher(app, drive)
    bundle = chain(scope)[0]
    old.freeze(scope, bundle, gen)
    marker_id, marker_sha = old.upload(scope, bundle["commit_id"], gen)  # then the old writer stalls
    frozen_before = app.frozen_intent(scope, bundle["commit_id"])

    new_gen = ops.enter_recovery()  # e.g. after a database restore
    assert new_gen == gen + 1
    with pytest.raises(contract.PipelineFailure) as excinfo:  # admissions closed
        body = request()
        app.reserve(scope=scope, idempotency_key="during-recovery",
                    request_digest=request_digest(scope, body), request=body)
    assert error(excinfo)["code"] == "PERSISTENCE_UNAVAILABLE"
    assert any(i["commit_id"] == bundle["commit_id"] for i in ops.unpublished_intents())
    ops.resume(new_gen)

    with pytest.raises(contract.PipelineFailure) as excinfo:  # the stalled writer resumes: fenced
        app.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
                             marker_sha256=marker_sha, generation=gen)
    assert error(excinfo)["code"] == "VERSION_CONFLICT"
    with pytest.raises(contract.PipelineFailure):
        app.mark_publishing(scope=scope, commit_id=bundle["commit_id"], generation=gen)

    app.adopt_intent(scope=scope, commit_id=bundle["commit_id"], generation=new_gen)
    frozen_after = app.frozen_intent(scope, bundle["commit_id"])
    assert frozen_after["marker"] == frozen_before["marker"]  # takeover never re-generates bytes
    assert frozen_after["object_ids"] == frozen_before["object_ids"]
    receipt = app.record_published(scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
                                   marker_sha256=marker_sha, generation=new_gen)
    assert receipt.registry_version == 1
    with pytest.raises(contract.PipelineFailure) as excinfo:  # no second commit for the slot, ever
        rival = deep(bundle)
        rival["commit_id"] = str(uuid.uuid4())
        TestPublisher(app, drive).freeze(scope, rival, new_gen)
    assert error(excinfo)["code"] == "VERSION_CONFLICT"


def test_recovery_waits_for_an_in_flight_commit(urls, app, ops, gen, scope, publisher):
    """enter_recovery cannot slip between an old writer's generation check and its commit."""
    bundle = chain(scope)[0]
    publisher.freeze(scope, bundle, gen)
    marker_id, marker_sha = publisher.upload(scope, bundle["commit_id"], gen)
    inside, release, done = threading.Event(), threading.Event(), []

    def pause(point):
        if point == "record_published:before_commit":
            inside.set()
            release.wait(10)

    slow = CoordinatorRepository(urls["app"], cursor_secret=SECRET, fault=pause)
    writer = threading.Thread(target=lambda: done.append(slow.record_published(
        scope=scope, commit_id=bundle["commit_id"], marker_object_id=marker_id,
        marker_sha256=marker_sha, generation=gen)))
    writer.start()
    assert inside.wait(10)
    recovery = threading.Thread(target=lambda: done.append(ops.enter_recovery()))
    recovery.start()
    recovery.join(0.5)
    assert recovery.is_alive()  # blocked behind the in-flight commit's share lock
    release.set()
    writer.join(10)
    recovery.join(10)
    ops.resume(done[-1])
    assert app.lookup_commit(scope, bundle["commit_id"]) is not None


# -- A07 projection outbox and search ------------------------------------------------------------

def drain(ops, worker="w1"):
    for claim in ops.claim_projections("search", worker, limit=100):
        ops.apply_search_projection(claim, worker)


def test_search_lags_but_get_is_current_and_projection_is_monotonic(app, ops, gen, scope, publisher):
    bundles = chain(scope, title="Invoice 2026")
    for b in bundles:
        publisher.publish(scope, b, gen)
    asset = bundles[0]["registry"]["asset_id"]
    assert app.search(scope, q=None, tag=None, status=None)["items"] == []  # projection not run yet
    assert app.get(scope, asset)["status"] == "completed"  # direct GET is current
    claims = sorted(ops.claim_projections("search", "w1", limit=100), key=lambda c: -c["registry_version"])
    for claim in claims:  # newest first: older revisions must not regress the row
        ops.apply_search_projection(claim, "w1")
    items = app.search(scope, q=None, tag=None, status=None)["items"]
    assert [(i["asset_id"], i["registry_version"]) for i in items] == [(asset, 3)]


def test_crashed_projector_claim_is_reclaimed(app, ops, gen, scope, publisher):
    publisher.publish(scope, chain(scope)[0], gen)
    claims = ops.claim_projections("search", "dead-worker", lease_seconds=0)
    assert claims
    time.sleep(0.05)
    retaken = ops.claim_projections("search", "w2")
    assert {c["commit_id"] for c in retaken} >= {c["commit_id"] for c in claims}
    assert ops.apply_search_projection(claims[0], "dead-worker") is False  # lost its lease
    for c in retaken:
        assert ops.apply_search_projection(c, "w2")


def test_failed_projection_backs_off_and_goes_dead(app, ops, gen, scope, publisher, monkeypatch):
    publisher.publish(scope, chain(scope)[0], gen)
    monkeypatch.setattr(repository_module, "MAX_PROJECTION_ATTEMPTS", 2)
    (claim,) = [c for c in ops.claim_projections("search", "w", limit=100) if c["tenant_id"] == scope.tenant_id]
    assert ops.fail_projection(claim, "w", "boom", base_delay=0) == "pending"
    time.sleep(0.05)
    (claim,) = [c for c in ops.claim_projections("search", "w", limit=100) if c["tenant_id"] == scope.tenant_id]
    assert ops.fail_projection(claim, "w", "boom", base_delay=0) == "dead"


def test_search_semantics(app, ops, gen, scope, publisher):
    rows = [("Budget 100%_final", "alpha", ["finance"]), ("Budget 100x final", "Bravo text", ["finance"]),
            ("Holiday photo", "contains LITERAL_underscore", ["travel"])]
    assets = []
    for title, text, tags in rows:
        for b in chain(scope, title=title, text=text, tags=tags):
            publisher.publish(scope, b, gen)
        assets.append(b["registry"]["asset_id"])
    drain(ops)

    def ids(**kw):
        return [i["asset_id"] for i in app.search(scope, **{"q": None, "tag": None, "status": None, **kw})["items"]]

    assert ids(q="100%_") == [assets[0]]  # % and _ are literal, not wildcards
    assert set(ids(q="budget")) == {assets[0], assets[1]}  # case-insensitive
    assert ids(q="bravo") == [assets[1]]  # matches search_text too
    assert ids(q="literal_UNDERSCORE") == [assets[2]]
    assert set(ids(tag="finance")) == {assets[0], assets[1]} and ids(tag="fin") == []  # tag is exact
    assert ids(status="failed") == []
    assert ids(q="budget", tag="travel") == []  # AND


def test_search_pagination_pins_the_snapshot(app, ops, gen, scope, publisher):
    for n in range(5):
        publisher.publish(scope, chain(scope, title=f"Doc {n}")[0], gen)
    drain(ops)
    page = app.search(scope, q="doc", tag=None, status=None, limit=2)
    seen = [i["asset_id"] for i in page["items"]]
    publisher.publish(scope, chain(scope, title="Doc late")[0], gen)
    drain(ops)  # projected after the snapshot: must not appear on later pages
    while page["next_cursor"]:
        page = app.search(scope, q="doc", tag=None, status=None, limit=2, cursor=page["next_cursor"])
        seen += [i["asset_id"] for i in page["items"]]
    assert len(seen) == len(set(seen)) == 5


def test_bad_cursors_are_invalid_requests(app, ops, gen, scope, publisher, monkeypatch):
    for n in range(3):
        publisher.publish(scope, chain(scope, title=f"C {n}")[0], gen)
    drain(ops)
    cursor = app.search(scope, q="c", tag=None, status=None, limit=1)["next_cursor"]
    tampered = cursor[:-2] + ("A" if cursor[-2] != "A" else "B") + cursor[-1]
    for bad, kwargs in ((tampered, {"q": "c"}), (cursor, {"q": "other"}),
                        (cursor, {"q": "c", "tag": "x"})):
        with pytest.raises(contract.PipelineFailure) as excinfo:
            app.search(scope, tag=kwargs.get("tag"), status=None, limit=1, cursor=bad, q=kwargs["q"])
        assert error(excinfo)["code"] == "INVALID_REQUEST"
    other_scope = contract.Scope(scope.tenant_id, "workspace-2")
    with pytest.raises(contract.PipelineFailure):
        app.search(other_scope, q="c", tag=None, status=None, limit=1, cursor=cursor)
    monkeypatch.setattr(repository_module, "CURSOR_TTL_SECONDS", -1)
    expired = app.search(scope, q="c", tag=None, status=None, limit=1)["next_cursor"]
    with pytest.raises(contract.PipelineFailure):
        app.search(scope, q="c", tag=None, status=None, limit=1, cursor=expired)


# -- A17 scope isolation, A16 reuse --------------------------------------------------------------

def test_other_scopes_cannot_see_or_reuse(app, ops, gen, scope, publisher, urls):
    bundles = chain(scope, title="Private")
    for b in bundles:
        publisher.publish(scope, b, gen, FINGERPRINT)
    drain(ops)
    asset = bundles[0]["registry"]["asset_id"]
    sha = bundles[2]["registry"]["identity"]["sha256"]
    for other in (contract.Scope(scope.tenant_id + "-x", scope.workspace_id),
                  contract.Scope(scope.tenant_id, "workspace-x")):
        with pytest.raises(contract.PipelineFailure) as excinfo:
            app.get(other, asset)
        assert error(excinfo)["code"] == "NOT_FOUND"
        assert app.search(other, q="private", tag=None, status=None)["items"] == []
        assert app.find_reusable(other, sha, FINGERPRINT) is None
        assert app.list_events(other, asset, 0, 10) == []
        with pytest.raises(contract.PipelineFailure):
            app.get_job(other, bundles[0]["registry"]["ingestion_id"])
        with pytest.raises(contract.PipelineFailure) as excinfo:  # explicit predicates, even when RLS is bypassed
            ops.get(other, asset)
        assert error(excinfo)["code"] == "NOT_FOUND"
        assert ops.search(other, q="private", tag=None, status=None)["items"] == []
        assert ops.find_reusable(other, sha, FINGERPRINT) is None
    with psycopg.connect(urls["app"]) as conn:  # raw SQL as the app role, no scope set
        assert conn.execute("SELECT count(*) FROM paios_ingest.registry_heads").fetchone()[0] == 0
    assert app.find_reusable(scope, sha, "e" * 64) is None  # different fingerprint: no reuse


# -- A20 append-only ----------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "UPDATE paios_ingest.audit_events SET event = '{}'",
    "DELETE FROM paios_ingest.audit_events",
    "DELETE FROM paios_ingest.registry_revisions",
    "UPDATE paios_ingest.master_index_revisions SET entry = '{}'",
    "TRUNCATE paios_ingest.audit_events",
])
def test_runtime_roles_cannot_rewrite_history(urls, gen, scope, publisher, sql):
    publisher.publish(scope, chain(scope)[0], gen)
    for role in ("app", "ops"):
        with psycopg.connect(urls[role]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)


def test_append_only_trigger_also_stops_the_owner(urls, gen, scope, publisher):
    publisher.publish(scope, chain(scope)[0], gen)
    with psycopg.connect(urls["admin"]) as conn:  # superuser: trigger still fires unless disabled
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM paios_ingest.audit_events")

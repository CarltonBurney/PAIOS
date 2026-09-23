"""PostgreSQL coordinator tests (fixture database and test adapters only; no cloud,
no live writes).

Requires PAIOS_TEST_DATABASE_URL: an admin connection string to a disposable
PostgreSQL server (CI uses a service container). Migrations run once into a
template database; every test gets its own clone, opened by the bootstrap path
(recovery -> reconcile -> resume). Repositories run as least-privilege roles, so
RLS and grants are real.
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
from persistence_helpers import admitted, chain, deep, payloads, request, set_title  # noqa: E402
from paios_ingestion import contract  # noqa: E402
from paios_ingestion.persistence import envelopes  # noqa: E402
from paios_ingestion.persistence import repository as repository_module  # noqa: E402
from paios_ingestion.persistence.canonical import request_digest, sha256_hex  # noqa: E402
from paios_ingestion.persistence.fakes import FakeDrive  # noqa: E402
from paios_ingestion.persistence.migrate import apply_migrations, grants_sql  # noqa: E402
from paios_ingestion.persistence.publisher import (PublicationService, Publisher, Reconciler,  # noqa: E402
                                                   Verifier, WorkerCredentials)
from paios_ingestion.persistence.repository import OBJECT_ROLES, CoordinatorRepository  # noqa: E402

ADMIN = os.environ.get("PAIOS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN, reason="PAIOS_TEST_DATABASE_URL not set")
SECRET = b"s" * 32
FINGERPRINT = "f" * 64


def _roles(conn):
    for role, bypass in (("paios_app", "NOBYPASSRLS"), ("paios_ops", "BYPASSRLS")):
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(f"{verb} ROLE {role} LOGIN NOSUPERUSER {bypass} PASSWORD '{role}'")


def _urls(name):
    admin = make_conninfo(ADMIN, dbname=name)
    return {"name": name, "admin": admin,
            "app": make_conninfo(admin, user="paios_app", password="paios_app"),
            "ops": make_conninfo(admin, user="paios_ops", password="paios_ops")}


def _clone(template):
    """A byte-for-byte copy of a database, like a restored backup (new database OID)."""
    name = f"paios_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    return _urls(name)


def _drop(name):
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="module")
def template():
    name = f"paios_tmpl_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
        _roles(conn)
    with psycopg.connect(_urls(name)["admin"], autocommit=True) as conn:
        apply_migrations(conn)
        conn.execute(grants_sql())
    yield name
    _drop(name)


@pytest.fixture
def fresh(template):
    """A migrated database that has never been opened."""
    urls = _clone(template)
    yield urls
    _drop(urls["name"])


@pytest.fixture
def urls(fresh):
    ops = CoordinatorRepository(fresh["ops"], cursor_secret=SECRET)
    assert ops.publisher_generation() == (1, "recovery")  # every install starts closed
    Reconciler(ops, FakeDrive()).run(1)  # bootstrap: nothing to reconcile, then resume
    return fresh


@pytest.fixture
def app(urls):
    return CoordinatorRepository(urls["app"], cursor_secret=SECRET)


@pytest.fixture
def ops(urls):
    return CoordinatorRepository(urls["ops"], cursor_secret=SECRET)


@pytest.fixture
def gen(ops):
    generation, mode = ops.publisher_generation()
    assert mode == "open"
    return generation


@pytest.fixture
def scope():
    return contract.Scope(f"tenant-{uuid.uuid4().hex[:8]}", "workspace-1")


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def publisher(app, drive):
    return Publisher(app, drive)


def error(excinfo):
    return error_of(excinfo)


def code(call):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        call()
    return error(excinfo)["code"]


def reserve(app, drive, scope, key="k1", body=None, **kwargs):
    body = body or request()
    return app.reserve(scope=scope, idempotency_key=key, request_digest=request_digest(scope, body),
                       request=body, record_object_id=drive.generate_ids(1)[0], **kwargs)


def _published(publisher, scope, gen):
    bundles = admitted(publisher, scope)
    for b in bundles:
        publisher.publish(scope, b, gen, processing_fingerprint=FINGERPRINT)
    return bundles


def crash_at(point):
    def fault(p):
        if p == point:
            raise RuntimeError(f"crash at {p}")
    return fault


# Round 2 B1-B5: tests use the real PostgreSQL fixture, never a cloud adapter.
def test_runtime_starts_closed_even_with_identical_open_database(app, ops, drive, scope):
    from paios_ingestion.persistence.runtime import FixtureRuntime
    assert app.serving_ready()
    first = FixtureRuntime(app, ops, drive)
    assert code(lambda: first.repositories(scope)) == 'PERSISTENCE_UNAVAILABLE'
    first.recover(complete_ledger=True, old_authority_terminated=True)
    ingestion, _ = first.repositories(scope)
    # A new process/composition root cannot inherit readiness from copied DB rows.
    copied = FixtureRuntime(app, ops, drive)
    assert app.serving_ready()
    assert code(lambda: copied.assets(scope)) == 'PERSISTENCE_UNAVAILABLE'
    assert code(lambda: copied.recover(complete_ledger=False, old_authority_terminated=True)) \
        == 'PERSISTENCE_UNAVAILABLE'
    assert not app.serving_ready()
    assert code(lambda: ingestion.lookup_commit(scope, str(uuid.uuid4()))) == 'PERSISTENCE_UNAVAILABLE'
    copied.recover(complete_ledger=True, old_authority_terminated=True)
    assert app.serving_ready()
    assert code(lambda: first.repositories(scope)) == 'PERSISTENCE_UNAVAILABLE'  # stale serving epoch


def test_quarantine_overrides_new_and_continued_search(app, ops, gen, scope, publisher):
    bundles = [admitted(publisher, scope, title=f'Doc {n}') for n in range(4)]
    for b in bundles:
        publisher.publish(scope, b[0], gen)
    drain(ops)
    query = dict(q='doc', tag=None, status=None, limit=1)
    first = app.search(scope, **query)
    first_id = first['items'][0]['asset_id']
    hidden = next(b[0]['registry']['asset_id'] for b in bundles if b[0]['registry']['asset_id'] != first_id)
    unrelated = next(b[0]['registry']['asset_id'] for b in bundles
                     if b[0]['registry']['asset_id'] not in (first_id, hidden))
    ops.quarantine(scope, hidden, 'test integrity failure')
    ops.quarantine(contract.Scope(scope.tenant_id, 'other-workspace'), unrelated, 'other scope')
    assert hidden not in {i['asset_id'] for i in _pages(app, scope, first, **query)}
    current = app.search(scope, **dict(query, limit=100))['items']
    assert hidden not in {i['asset_id'] for i in current}
    assert unrelated in {i['asset_id'] for i in current}


@pytest.mark.parametrize('tamper', ['delete', 'edit'])
@pytest.mark.parametrize('committed', [False, True])
def test_verifier_checks_reservations_before_and_after_acceptance(app, ops, drive, gen, scope,
                                                               publisher, tamper, committed):
    bundles = admitted(publisher, scope)
    if committed:
        publisher.publish(scope, bundles[0], gen)
    r = app.reservation_record(scope, bundles[0]['registry']['ingestion_id'])
    if tamper == 'delete':
        drive.delete(r['record_object_id'])
    else:
        drive.update_content(r['record_object_id'], b'{}')
    result = Verifier(ops, drive).run()
    assert result and 'reservation.json' in result[0][2]
    assert ops.quarantined(scope, r['asset_id'])


def test_imports_reject_relocated_envelopes(app, ops, drive, gen, scope, publisher):
    bundles = admitted(publisher, scope)
    publisher.freeze(scope, bundles[0], gen)
    record = app.reservation_record(scope, bundles[0]['registry']['ingestion_id'])
    frozen = app.frozen_intent(scope, bundles[0]['commit_id'])
    generation = ops.enter_recovery()
    assert code(lambda: ops.import_reservation(record['record_bytes'], generation,
                                              source_object_id='wrong-id')) == 'INTEGRITY_FAILED'
    assert code(lambda: ops.import_intent(frozen['intent'], generation,
                                         source_object_id='wrong-id')) == 'INTEGRITY_FAILED'


def test_cursor_expiry_is_not_extended_by_paging(app, ops, gen, scope, publisher, monkeypatch):
    from datetime import datetime, timedelta, timezone
    class Clock(datetime):
        offset = 0
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(seconds=cls.offset)
    monkeypatch.setattr(repository_module, 'datetime', Clock)
    for n in range(4):
        publisher.publish(scope, admitted(publisher, scope, title=f'Page {n}')[0], gen)
    drain(ops)
    query = dict(q='page', tag=None, status=None, limit=1)
    first = app.search(scope, **query)
    Clock.offset = repository_module.CURSOR_TTL_SECONDS - 1
    second = app.search(scope, **query, cursor=first['next_cursor'])
    import base64, json
    def state(token):
        return json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))[32:])
    assert state(first['next_cursor'])['expires'] == state(second['next_cursor'])['expires']
    assert state(first['next_cursor'])['indexed_at'] == state(second['next_cursor'])['indexed_at']
    Clock.offset = repository_module.CURSOR_TTL_SECONDS + 1
    assert code(lambda: app.search(scope, **query, cursor=second['next_cursor'])) == 'INVALID_REQUEST'


def test_issued_protocol_adapters_commit_and_projection(app, ops, drive, scope):
    from paios_ingestion.persistence.runtime import FixtureRuntime
    runtime = FixtureRuntime(app, ops, drive)
    runtime.recover(complete_ledger=True, old_authority_terminated=True)
    ingestion, index = runtime.repositories(scope)
    body = request()
    args = dict(scope=scope, idempotency_key='protocol', request_digest=request_digest(scope, body), request=body)
    reservation = ingestion.reserve(**args)
    assert ingestion.reserve(**args).asset_id == reservation.asset_id
    bundles = chain(scope, ids=dict(asset_id=reservation.asset_id, ingestion_id=reservation.ingestion_id,
                                   commit_id=str(uuid.uuid4())))
    receipts = []
    for n, bundle in enumerate(bundles):
        receipts.append(ingestion.commit(bundle=bundle, expected_version=n))
    assert ingestion.commit(bundle=bundles[0], expected_version=0) == receipts[0]
    assert code(lambda: ingestion.commit(bundle=bundles[2], expected_version=0)) == 'VERSION_CONFLICT'
    for n in (2, 0, 1, 2):
        index.apply_committed(receipts[n], bundles[n]['index'])
    result = index.search(scope, q=None, tag=None, status=None, limit=10, cursor=None)
    assert result['items'][0]['registry_version'] == 3
    bad = deep(bundles[2]['index'])
    bad['search_text'] = 'uncommitted'
    assert code(lambda: index.apply_committed(receipts[2], bad)) == 'INTEGRITY_FAILED'
    assert ingestion.get_job(scope, reservation.ingestion_id)['status'] == 'completed'
    assert ingestion.lookup_commit(scope, bundles[2]['commit_id']) == receipts[2]


# -- migrations ---------------------------------------------------------------------

def test_migrations_are_idempotent_and_tamper_evident(urls):
    with psycopg.connect(urls["admin"], autocommit=True) as conn:
        assert apply_migrations(conn) == []
        for name in ("0001_foundation.sql", "0002_recovery_and_snapshots.sql"):
            conn.execute("UPDATE paios_ingest.schema_migrations SET sha256 = 'x' WHERE name = %s", (name,))
            with pytest.raises(RuntimeError):
                apply_migrations(conn)
            original = resources.files("paios_ingestion.persistence.migrations").joinpath(name)
            conn.execute("UPDATE paios_ingest.schema_migrations SET sha256 = %s WHERE name = %s",
                         (hashlib.sha256(original.read_bytes()).hexdigest(), name))


def test_intent_rows_require_all_five_object_ids(urls):
    with psycopg.connect(urls["admin"]) as conn:
        check = conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                             "WHERE conname = 'commit_intents_object_roles'").fetchone()[0]
    assert all(role in check for role in OBJECT_ROLES)


# -- A02 reservations, R1 reservation record -----------------------------------------------

def test_reservation_replay_and_conflict(app, drive, gen, scope):
    first = reserve(app, drive, scope, pinned={"provider_profile_version": "3"})
    again = reserve(app, drive, scope)
    assert (again.ingestion_id, again.asset_id, again.replay) == (first.ingestion_id, first.asset_id, True)
    assert first.replay is False
    assert app.reservation_pins(scope, first.ingestion_id) == {"provider_profile_version": "3"}
    record = app.reservation_record(scope, first.ingestion_id)
    assert envelopes.parse_reservation(record["record_bytes"])["ingestion_id"] == first.ingestion_id
    assert code(lambda: reserve(app, drive, scope, body=request(item="item-2"))) == "IDEMPOTENCY_CONFLICT"
    elsewhere = contract.Scope(scope.tenant_id + "-b", scope.workspace_id)
    assert reserve(app, drive, elsewhere).asset_id != first.asset_id  # keys are scoped
    assert code(lambda: app.reserve(scope=scope, idempotency_key="k9", request_digest="0" * 64,
                                    request=request(), record_object_id=drive.generate_ids(1)[0])) \
        == "CONTRACT_MISMATCH"


def test_concurrent_reservations_allocate_ids_once(app, drive, gen, scope):
    results = []
    threads = [threading.Thread(target=lambda: results.append(reserve(app, drive, scope, key="race")))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.asset_id for r in results}) == 1
    assert sum(not r.replay for r in results) == 1


def test_reservation_record_is_published_before_acceptance(app, drive, gen, scope, publisher):
    reservation = reserve(app, drive, scope)
    record = app.reservation_record(scope, reservation.ingestion_id)
    bundles = chain(scope, ids={"asset_id": reservation.asset_id, "ingestion_id": reservation.ingestion_id,
                                "commit_id": record["first_commit_id"]})
    assert code(lambda: publisher.freeze(scope, bundles[0], gen)) == "CONTRACT_MISMATCH"
    publisher.publish_reservation(scope, reservation.ingestion_id)
    stored = drive.metadata(record["record_object_id"])
    assert stored["sha256Checksum"] == sha256_hex(record["record_bytes"])
    assert stored["appProperties"]["role"] == "reservation"
    wrong = deep(bundles[0])
    wrong["commit_id"] = str(uuid.uuid4())  # revision 1 must use the reserved first commit ID
    assert code(lambda: publisher.freeze(scope, wrong, gen)) == "CONTRACT_MISMATCH"
    assert publisher.publish(scope, bundles[0], gen).registry_version == 1


# -- A01 publication chain, reads, jobs ---------------------------------------------------

def test_chain_publishes_and_reads_latest(app, drive, gen, scope, publisher):
    bundles = admitted(publisher, scope)
    receipts = [publisher.publish(scope, b, gen, processing_fingerprint=FINGERPRINT) for b in bundles]
    asset = bundles[0]["registry"]["asset_id"]
    assert [r.registry_version for r in receipts] == [1, 2, 3]
    assert app.get(scope, asset) == bundles[2]["registry"]
    events = app.list_events(scope, asset, 0, 10)
    assert [e["event_type"] for e in events] == ["asset_accepted", "processing_started", "ingestion_completed"]
    job = app.get_job(scope, bundles[0]["registry"]["ingestion_id"])
    assert (job["status"], job["registry_version"]) == ("completed", 3)
    assert app.lookup_commit(scope, bundles[1]["commit_id"]) == receipts[1]
    assert app.find_reusable(scope, bundles[2]["registry"]["identity"]["sha256"], FINGERPRINT)["asset_id"] == asset


def test_intent_json_is_first_and_holds_the_frozen_bytes(app, drive, gen, scope, publisher):
    bundle = admitted(publisher, scope)[0]
    publisher.publish(scope, bundle, gen)
    frozen = app.frozen_intent(scope, bundle["commit_id"])
    creates = [file_id for op, file_id in drive.calls if op == "create"]
    ids = frozen["object_ids"]
    assert creates[-5:] == [ids["intent.json"], ids["registry.json"], ids["audit.json"], ids["index.json"],
                            ids["commit.json"]]
    parsed = envelopes.parse_intent(drive.download(ids["intent.json"]))
    assert parsed["payload_bytes"] == frozen["payloads"] and parsed["marker_bytes"] == frozen["marker"]
    assert drive.download(ids["intent.json"]) == frozen["intent"]


# -- A03 races and stale writers ----------------------------------------------------------

def test_one_commit_per_revision_slot(app, gen, scope, publisher):
    bundles = admitted(publisher, scope)
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
    bundle = admitted(publisher, scope)[0]
    ids = publisher.freeze(scope, bundle, gen)
    assert publisher.freeze(scope, bundle, gen) == ids  # a retry keeps the frozen IDs
    frozen = app.frozen_intent(scope, bundle["commit_id"])
    parts = payloads(bundle)
    assert app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts, object_ids=ids,
                             marker=frozen["marker"], generation=gen) == "frozen"
    assert app.frozen_intent(scope, bundle["commit_id"])["intent"] == frozen["intent"]  # original bytes kept
    later = envelopes.build_marker(scope=scope, bundle=bundle, object_ids=ids, payloads=parts,
                                   previous_marker_sha=None, published_at="2026-09-23T07:00:00Z")
    assert code(lambda: app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts,
                                          object_ids=ids, marker=later, generation=gen)) == "INTEGRITY_FAILED"
    changed = set_title(deep(bundle), "Changed")
    assert code(lambda: publisher.freeze(scope, changed, gen)) == "INTEGRITY_FAILED"


def test_stale_expected_version_is_rejected(app, gen, scope, publisher):
    bundles = admitted(publisher, scope)
    publisher.publish(scope, bundles[0], gen)
    assert code(lambda: publisher.freeze(scope, bundles[2], gen)) == "VERSION_CONFLICT"


# -- A21 / P2-A frozen intent integrity -----------------------------------------------------

def test_invalid_triple_is_rejected_before_freezing(app, gen, scope, publisher):
    bundle = deep(admitted(publisher, scope)[0])
    bundle["index"]["status"] = "processing"
    assert code(lambda: publisher.freeze(scope, bundle, gen)) == "INTEGRITY_FAILED"


@pytest.mark.parametrize("broken", ["previous", "file_sha", "object_id", "non_canonical", "four_ids"])
def test_marker_must_match_the_frozen_bundle(app, drive, gen, scope, publisher, broken):
    bundle = admitted(publisher, scope)[0]
    parts = payloads(bundle)
    ids = dict(zip(OBJECT_ROLES, drive.generate_ids(5)))
    mark = envelopes.build_marker(scope=scope, bundle=bundle, object_ids=ids, payloads=parts,
                                  previous_marker_sha="a" * 64 if broken == "previous" else None,
                                  published_at="2026-09-23T06:00:00Z")
    if broken == "file_sha":
        mark = mark.replace(sha256_hex(parts["index.json"]).encode(), b"0" * 64)
    if broken == "object_id":
        mark = mark.replace(ids["audit.json"].encode(), b"drive-other")
    if broken == "non_canonical":
        mark = mark.replace(b'","', b'", "', 1)
    if broken == "four_ids":
        del ids["intent.json"]
    expected = "CONTRACT_MISMATCH" if broken == "four_ids" else "INTEGRITY_FAILED"
    assert code(lambda: app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts,
                                          object_ids=ids, marker=mark, generation=gen)) == expected


def test_non_canonical_payload_bytes_are_rejected(app, drive, gen, scope, publisher):
    bundle = admitted(publisher, scope)[0]
    parts = payloads(bundle)
    parts["index.json"] = parts["index.json"].replace(b":", b": ", 1)
    ids = dict(zip(OBJECT_ROLES, drive.generate_ids(5)))
    assert code(lambda: app.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts,
                                          object_ids=ids, marker=b"{}", generation=gen)) == "INTEGRITY_FAILED"


# -- A04 crash injection in the visibility transaction ----------------------------------------

@pytest.mark.parametrize("point", ["record_published:before_head", "record_published:after_head",
                                   "record_published:after_revisions", "record_published:before_commit"])
def test_crash_inside_record_published_leaves_nothing_visible(urls, drive, gen, scope, point):
    crashing = CoordinatorRepository(urls["app"], cursor_secret=SECRET, fault=crash_at(point))
    healthy = CoordinatorRepository(urls["app"], cursor_secret=SECRET)
    bundle = admitted(Publisher(healthy, drive), scope)[0]
    pub = Publisher(crashing, drive)
    pub.freeze(scope, bundle, gen)
    with pytest.raises(RuntimeError):
        pub.execute(scope, bundle["commit_id"], gen)
    asset = bundle["registry"]["asset_id"]
    assert code(lambda: healthy.get(scope, asset)) == "PERSISTENCE_UNAVAILABLE"  # marker is out: not 404
    assert healthy.list_events(scope, asset, 0, 10) == []
    assert healthy.lookup_commit(scope, bundle["commit_id"]) is None
    receipt = Publisher(healthy, drive).execute(scope, bundle["commit_id"], gen)  # the retry completes
    assert receipt.registry_version == 1
    assert len(healthy.list_events(scope, asset, 0, 10)) == 1  # exactly one audit revision


# -- A05 lost response after commit ---------------------------------------------------------

def test_record_published_replay_returns_the_same_receipt(app, gen, scope, publisher):
    bundle = admitted(publisher, scope)[0]
    first = publisher.publish(scope, bundle, gen)
    frozen = app.frozen_intent(scope, bundle["commit_id"])
    again = app.record_published(scope=scope, commit_id=bundle["commit_id"],
                                 marker_object_id=frozen["object_ids"]["commit.json"],
                                 marker_sha256=sha256_hex(frozen["marker"]), generation=gen)
    assert again == first == publisher.execute(scope, bundle["commit_id"], gen)
    assert code(lambda: app.record_published(
        scope=scope, commit_id=bundle["commit_id"], marker_object_id=frozen["object_ids"]["commit.json"],
        marker_sha256="0" * 64, generation=gen)) == "INTEGRITY_FAILED"


def test_drive_lost_response_retries_with_the_same_ids(app, drive, gen, scope, publisher):
    bundle = admitted(publisher, scope)[0]
    drive.faults.inject("create", "lost_response", "fail_before")
    publisher.publish(scope, bundle, gen)
    frozen = app.frozen_intent(scope, bundle["commit_id"])
    parent = f"{scope.tenant_id}/{scope.workspace_id}/{bundle['registry']['asset_id']}"
    stored = {m["id"] for m in drive.list_children(parent) if m["appProperties"]["role"] != "reservation"}
    assert stored == set(frozen["object_ids"].values())  # no duplicates, nothing regenerated


def test_drive_object_with_other_bytes_is_integrity_failure(app, drive, gen, scope, publisher):
    bundle = admitted(publisher, scope)[0]
    ids = publisher.freeze(scope, bundle, gen)
    drive.create(ids["registry.json"], name="registry.json", parent="x", data=b"{}")  # squatted ID
    assert code(lambda: publisher.execute(scope, bundle["commit_id"], gen)) == "INTEGRITY_FAILED"


def test_recording_requires_the_publishing_mark(app, gen, scope, publisher):
    bundle = admitted(publisher, scope)[0]
    ids = publisher.freeze(scope, bundle, gen)
    frozen = app.frozen_intent(scope, bundle["commit_id"])
    assert code(lambda: app.record_published(scope=scope, commit_id=bundle["commit_id"],
                                             marker_object_id=ids["commit.json"],
                                             marker_sha256=sha256_hex(frozen["marker"]),
                                             generation=gen)) == "CONTRACT_MISMATCH"


# -- P2-C reads during uncertain publication ----------------------------------------------------

def test_get_refuses_a_possibly_stale_head_while_publishing(app, gen, scope, publisher):
    bundles = admitted(publisher, scope)
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
    assert code(lambda: app.get_job(scope, bundles[0]["registry"]["ingestion_id"])) == "PERSISTENCE_UNAVAILABLE"
    app.record_published(scope=scope, commit_id=bundles[1]["commit_id"], marker_object_id=marker_id,
                         marker_sha256=marker_sha, generation=gen)
    assert app.get(scope, asset)["status"] == "processing"


# -- U2: head and uncertainty are read from one snapshot -----------------------------------------

@pytest.mark.parametrize("read", ["get", "get_job"])
def test_publication_between_reads_never_yields_the_obsolete_head(urls, app, gen, scope, publisher, read):
    """Review schedule: revision N's marker is already in the DGE and its intent is
    publishing; the reader takes its first read (head N-1); record_published(N)
    commits; the reader takes its second read. Accept N or 503, never N-1."""
    bundles = admitted(publisher, scope)
    publisher.publish(scope, bundles[0], gen)
    publisher.freeze(scope, bundles[1], gen)
    marker_id, marker_sha = publisher.upload(scope, bundles[1]["commit_id"], gen)  # N visible in the DGE
    committed = []

    def barrier(point):
        if point.endswith(":between_reads") and not committed:
            committed.append(app.record_published(scope=scope, commit_id=bundles[1]["commit_id"],
                                                  marker_object_id=marker_id, marker_sha256=marker_sha,
                                                  generation=gen))

    reader = CoordinatorRepository(urls["app"], cursor_secret=SECRET, fault=barrier)
    if read == "get":
        call = lambda: reader.get(scope, bundles[0]["registry"]["asset_id"])["canonical_record_version"]  # noqa: E731
    else:
        call = lambda: reader.get_job(scope, bundles[0]["registry"]["ingestion_id"])["registry_version"]  # noqa: E731
    try:
        version = call()
    except contract.PipelineFailure as exc:
        assert (exc.error["code"], exc.error["retryable"]) == ("PERSISTENCE_UNAVAILABLE", True)
    else:
        assert version == 2, "an obsolete head was returned after N was published"
    assert committed, "the barrier did not run between the two reads"
    assert app.get(scope, bundles[0]["registry"]["asset_id"])["canonical_record_version"] == 2


# -- U1: recovery and restore close authoritative reads ------------------------------------------

def _reads(repo, scope, bundles):
    asset, ingestion = bundles[0]["registry"]["asset_id"], bundles[0]["registry"]["ingestion_id"]
    return {"get": lambda: repo.get(scope, asset), "get_job": lambda: repo.get_job(scope, ingestion),
            "list_events": lambda: repo.list_events(scope, asset, 0, 10),
            "lookup_commit": lambda: repo.lookup_commit(scope, bundles[0]["commit_id"]),
            "find_reusable": lambda: repo.find_reusable(scope, bundles[2]["registry"]["identity"]["sha256"],
                                                        FINGERPRINT),
            "search": lambda: repo.search(scope, q=None, tag=None, status=None),
            "reservation_pins": lambda: repo.reservation_pins(scope, ingestion)}


def test_recovery_closes_reads_of_existing_assets_and_jobs(app, ops, drive, gen, scope, publisher):
    bundles = _published(publisher, scope, gen)
    for read in _reads(app, scope, bundles).values():
        read()  # all served while open
    new_gen = ops.enter_recovery()
    for name, read in _reads(app, scope, bundles).items():
        with pytest.raises(contract.PipelineFailure) as excinfo:
            read()
        err = error(excinfo)
        assert (err["code"], err["retryable"]) == ("PERSISTENCE_UNAVAILABLE", True), name
    assert code(lambda: reserve(app, drive, scope, key="during")) == "PERSISTENCE_UNAVAILABLE"
    Reconciler(ops, drive).run(new_gen)
    assert app.get(scope, bundles[0]["registry"]["asset_id"]) == bundles[2]["registry"]
    assert app.get_job(scope, bundles[0]["registry"]["ingestion_id"])["status"] == "completed"


def test_fresh_install_starts_closed(fresh):
    app = CoordinatorRepository(fresh["app"], cursor_secret=SECRET)
    scope = contract.Scope("t", "w")
    assert code(lambda: app.get(scope, str(uuid.uuid4()))) == "PERSISTENCE_UNAVAILABLE"
    assert code(lambda: app.search(scope, q=None, tag=None, status=None)) == "PERSISTENCE_UNAVAILABLE"


def test_restored_database_starts_closed_until_reconciled(urls, app, drive, gen, scope, publisher):
    """A copy of an open database (like a restored backup) still says mode='open',
    but it is a different database, so nothing is served until reconciliation."""
    bundles = _published(publisher, scope, gen)
    restored = _clone(urls["name"])
    try:
        with psycopg.connect(restored["admin"]) as conn:
            assert conn.execute("SELECT mode FROM paios_ingest.publisher_state").fetchone()[0] == "open"
        r_app = CoordinatorRepository(restored["app"], cursor_secret=SECRET)
        r_ops = CoordinatorRepository(restored["ops"], cursor_secret=SECRET)
        for name, read in _reads(r_app, scope, bundles).items():
            assert code(read) == "PERSISTENCE_UNAVAILABLE", name
        assert code(lambda: reserve(r_app, drive, scope, key="restored")) == "PERSISTENCE_UNAVAILABLE"
        Reconciler(r_ops, drive).run(r_ops.enter_recovery())
        assert r_app.get(scope, bundles[0]["registry"]["asset_id"]) == bundles[2]["registry"]
        assert app.get(scope, bundles[0]["registry"]["asset_id"]) == bundles[2]["registry"]  # original unaffected
    finally:
        _drop(restored["name"])


def test_resume_is_refused_until_uncertain_intents_are_resolved(app, ops, drive, gen, scope, publisher):
    bundles = admitted(publisher, scope)
    publisher.publish(scope, bundles[0], gen)
    publisher.freeze(scope, bundles[1], gen)
    publisher.upload(scope, bundles[1]["commit_id"], gen)  # marker out, then the writer dies
    new_gen = ops.enter_recovery()
    assert code(lambda: ops.resume(new_gen)) == "CONTRACT_MISMATCH"
    assert code(lambda: app.get(scope, bundles[0]["registry"]["asset_id"])) == "PERSISTENCE_UNAVAILABLE"
    report = Reconciler(ops, drive).run(new_gen)
    assert bundles[1]["commit_id"] in report["completed"]
    assert app.get(scope, bundles[0]["registry"]["asset_id"])["status"] == "processing"


def test_reconciliation_path_needs_recovery_mode_and_the_privileged_role(app, ops, drive, gen, scope, publisher):
    bundle = admitted(publisher, scope)[0]
    publisher.freeze(scope, bundle, gen)
    assert code(lambda: ops.adopt_intent(scope=scope, commit_id=bundle["commit_id"], generation=gen)) \
        == "CONTRACT_MISMATCH"  # open mode: no reconciliation path
    new_gen = ops.enter_recovery()
    assert code(lambda: app.adopt_intent(scope=scope, commit_id=bundle["commit_id"], generation=new_gen)) \
        == "CONTRACT_MISMATCH"  # ordinary worker role
    assert code(lambda: app.mark_publishing(scope=scope, commit_id=bundle["commit_id"], generation=new_gen,
                                            reconcile=True)) == "CONTRACT_MISMATCH"
    assert code(lambda: app.quarantine(scope, bundle["registry"]["asset_id"], "x")) == "CONTRACT_MISMATCH"
    Reconciler(ops, drive).run(new_gen)


# -- P2-B recovery fences old publishers and completes their intents while closed -------------

def test_recovery_fences_an_old_writer_and_completes_its_intent_while_closed(app, ops, drive, gen, scope,
                                                                             publisher):
    bundle = admitted(publisher, scope)[0]
    publisher.freeze(scope, bundle, gen)
    marker_id, marker_sha = publisher.upload(scope, bundle["commit_id"], gen)  # then the old writer stalls
    frozen_before = app.frozen_intent(scope, bundle["commit_id"])
    objects_before = {role: drive.download(i) for role, i in frozen_before["object_ids"].items()}

    new_gen = ops.enter_recovery()
    assert new_gen == gen + 1
    assert code(lambda: app.record_published(scope=scope, commit_id=bundle["commit_id"],
                                             marker_object_id=marker_id, marker_sha256=marker_sha,
                                             generation=gen)) == "PERSISTENCE_UNAVAILABLE"
    assert code(lambda: app.mark_publishing(scope=scope, commit_id=bundle["commit_id"], generation=gen)) \
        == "PERSISTENCE_UNAVAILABLE"
    assert any(i["commit_id"] == bundle["commit_id"] for i in ops.unpublished_intents())

    seen_modes = []
    reconciler = Reconciler(ops, drive, fault=lambda p: seen_modes.append(ops.publisher_generation()[1]))
    report = reconciler.run(new_gen, resume=False)  # completes the intent with no public resume
    assert report["completed"] == [bundle["commit_id"]]
    assert seen_modes and set(seen_modes) == {"recovery"}
    assert code(lambda: app.get(scope, bundle["registry"]["asset_id"])) == "PERSISTENCE_UNAVAILABLE"
    frozen_after = ops.frozen_intent(scope, bundle["commit_id"])
    assert (frozen_after["marker"], frozen_after["object_ids"], frozen_after["intent"]) == \
        (frozen_before["marker"], frozen_before["object_ids"], frozen_before["intent"])
    assert {role: drive.download(i) for role, i in frozen_after["object_ids"].items()} == objects_before
    assert frozen_after["state"] == "published"
    ops.resume(new_gen)

    assert app.get(scope, bundle["registry"]["asset_id"]) == bundle["registry"]
    assert code(lambda: app.record_published(scope=scope, commit_id=bundle["commit_id"],
                                             marker_object_id=marker_id, marker_sha256="0" * 64,
                                             generation=gen)) == "INTEGRITY_FAILED"
    stale = admitted(publisher, scope)[0]
    assert code(lambda: publisher.freeze(scope, stale, gen)) == "VERSION_CONFLICT"  # old generation fenced


def test_recovery_waits_for_an_in_flight_commit(urls, app, ops, drive, gen, scope, publisher):
    """enter_recovery cannot slip between an old writer's generation check and its commit."""
    bundle = admitted(publisher, scope)[0]
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
    Reconciler(ops, drive).run(done[-1])
    assert app.lookup_commit(scope, bundle["commit_id"]) is not None


def test_marker_published_but_record_lost_is_completed_by_reconciliation(app, ops, drive, gen, scope):
    bundle = admitted(Publisher(app, drive), scope)[0]
    dying = Publisher(app, drive, fault=crash_at("publisher:after_marker"))
    with pytest.raises(RuntimeError):
        dying.publish(scope, bundle, gen)
    assert code(lambda: app.get(scope, bundle["registry"]["asset_id"])) == "PERSISTENCE_UNAVAILABLE"
    Reconciler(ops, drive).run(ops.enter_recovery())
    assert app.get(scope, bundle["registry"]["asset_id"]) == bundle["registry"]


# -- R1: coordinator loss is recovered from the DGE ------------------------------------------

def test_coordinator_loss_rebuilds_from_dge_without_reallocating(template, app, ops, drive, gen, scope,
                                                                publisher):
    done = admitted(publisher, scope, key="loss-key")
    for b in done:
        publisher.publish(scope, b, gen)
    partial = admitted(publisher, scope)
    publisher.publish(scope, partial[0], gen)
    publisher.freeze(scope, partial[1], gen)
    stalled = Publisher(app, drive, fault=crash_at("publisher:before_marker"))
    with pytest.raises(RuntimeError):
        stalled.execute(scope, partial[1]["commit_id"], gen)  # intent.json and payloads out, no marker
    reserved_only = reserve(app, drive, scope, key="db-only")  # never reached the DGE: never accepted
    partial_marker = app.frozen_intent(scope, partial[1]["commit_id"])["object_ids"]["commit.json"]
    assert not drive.query(role="commit", commit_id=partial[1]["commit_id"])

    lost = _clone(template)  # a brand-new empty coordinator; the DGE is all that survives
    try:
        n_ops = CoordinatorRepository(lost["ops"], cursor_secret=SECRET)
        n_app = CoordinatorRepository(lost["app"], cursor_secret=SECRET)
        report = Reconciler(n_ops, drive).run(n_ops.enter_recovery())
        assert report["reservations_imported"] == 2
        assert report["intents_imported"] == 5
        # every imported intent is re-recorded from verified DGE objects, with its original commit ID
        assert sorted(report["completed"]) == sorted(b["commit_id"] for b in done + partial[:2])
        assert drive.metadata(partial_marker)["sha256Checksum"] == \
            sha256_hex(ops.frozen_intent(scope, partial[1]["commit_id"])["marker"])  # finished, not reallocated
        assert report["quarantined"] == []
        assert n_app.get(scope, done[0]["registry"]["asset_id"]) == done[2]["registry"]
        assert n_app.get(scope, partial[0]["registry"]["asset_id"]) == partial[1]["registry"]
        for bundle in done + partial[:2]:
            assert n_ops.frozen_intent(scope, bundle["commit_id"])["intent"] == \
                ops.frozen_intent(scope, bundle["commit_id"])["intent"]
        assert code(lambda: n_app.reservation_pins(scope, reserved_only.ingestion_id)) == "NOT_FOUND"
        body = request(item="loss-key")  # admitted() uses the key as the request's item ID
        replay = Publisher(n_app, drive).admit(scope, idempotency_key="loss-key", request=body,
                                               request_digest=request_digest(scope, body))
        assert (replay.replay, replay.asset_id) == (True, done[0]["registry"]["asset_id"])
    finally:
        _drop(lost["name"])


def test_second_dge_intent_for_a_slot_quarantines_the_asset(app, ops, drive, gen, scope, publisher):
    bundles = admitted(publisher, scope)
    publisher.publish(scope, bundles[0], gen)
    publisher.freeze(scope, bundles[1], gen)
    rival = deep(bundles[1])  # e.g. written by a writer that escaped fencing: a fork
    rival["commit_id"] = str(uuid.uuid4())
    rival_ids = dict(zip(OBJECT_ROLES, drive.generate_ids(5)))
    parts = payloads(rival)
    head = app.frozen_intent(scope, bundles[0]["commit_id"])
    rival_marker = envelopes.build_marker(scope=scope, bundle=rival, object_ids=rival_ids, payloads=parts,
                                          previous_marker_sha=sha256_hex(head["marker"]),
                                          published_at="2026-09-23T06:00:00Z")
    rival_intent = envelopes.build_intent(scope=scope, commit_id=rival["commit_id"], registry=rival["registry"],
                                          object_ids=rival_ids, payloads=parts, marker=rival_marker,
                                          frozen_at="2026-09-23T06:00:00Z")
    drive.create(rival_ids["intent.json"], name="intent.json", parent="fork", data=rival_intent,
                 app_properties={"role": "intent"})
    report = Reconciler(ops, drive).run(ops.enter_recovery())
    asset = bundles[0]["registry"]["asset_id"]
    assert (scope.tenant_id, scope.workspace_id, asset) in report["quarantined"]
    assert code(lambda: app.get(scope, asset)) == "INTEGRITY_FAILED"  # nothing picks a winner
    assert "slot 2" in ops.quarantined(scope, asset)


def test_marker_without_an_intent_quarantines_the_asset(app, ops, drive, gen, scope, publisher):
    bundles = _published(publisher, scope, gen)
    frozen = app.frozen_intent(scope, bundles[2]["commit_id"])
    outside = drive.generate_ids(1)[0]
    stray = frozen["marker"].replace(bundles[2]["commit_id"].encode(), str(uuid.uuid4()).encode())
    drive.create(outside, name="commit.json", parent="x", data=stray, app_properties={"role": "commit"})
    Reconciler(ops, drive).run(ops.enter_recovery())
    assert code(lambda: app.get(scope, bundles[0]["registry"]["asset_id"])) == "INTEGRITY_FAILED"


# -- P2-E / R4 detection and the publication-service boundary ------------------------------

@pytest.mark.parametrize("tamper", ["edit", "delete"])
def test_verifier_detects_edited_and_deleted_objects(app, ops, drive, gen, scope, publisher, tamper):
    bundles = _published(publisher, scope, gen)
    target = app.frozen_intent(scope, bundles[1]["commit_id"])["object_ids"]["audit.json"]
    if tamper == "edit":
        drive.update_content(target, b'{"tampered":true}')
    else:
        drive.delete(target)
    found = Verifier(ops, drive).run()
    asset = bundles[0]["registry"]["asset_id"]
    assert [(s, a) for s, a, _ in found] == [(scope, asset)]
    assert ("edited" if tamper == "edit" else "missing") in ops.quarantined(scope, asset)
    assert code(lambda: app.get(scope, asset)) == "INTEGRITY_FAILED"
    assert app.find_reusable(scope, bundles[2]["registry"]["identity"]["sha256"], FINGERPRINT) is None


def test_publication_service_is_the_only_writer_and_checks_scope(app, drive, gen, scope):
    credentials = WorkerCredentials(b"k" * 32)
    service = PublicationService(Publisher(app, drive), credentials, gen)
    assert sorted(m for m in dir(service) if not m.startswith("_")) == ["admit", "commit"]  # no update/delete
    token = credentials.issue("worker-1", scope)
    body = request()
    reservation = service.admit(token, scope=scope, idempotency_key="svc", request=body,
                                request_digest=request_digest(scope, body))
    first_commit = app.reservation_record(scope, reservation.ingestion_id)["first_commit_id"]
    bundles = chain(scope, ids={"asset_id": reservation.asset_id, "ingestion_id": reservation.ingestion_id,
                                "commit_id": first_commit})
    other = contract.Scope(scope.tenant_id, "workspace-other")
    for bad in (credentials.issue("worker-1", other), token[:-4] + "AAAA",
                credentials.issue("worker-1", scope, ttl_seconds=-1)):
        assert code(lambda: service.commit(bad, scope=scope, bundle=bundles[0], expected_version=0)) \
            == "CONTRACT_MISMATCH"
    assert code(lambda: service.commit(credentials.issue("worker-1", other), scope=other, bundle=bundles[0],
                                       expected_version=0)) == "CONTRACT_MISMATCH"  # bundle scope differs
    assert code(lambda: service.commit(token, scope=scope, bundle=bundles[0],
                                       expected_version=2)) == "VERSION_CONFLICT"
    assert service.commit(token, scope=scope, bundle=bundles[0], expected_version=0).registry_version == 1


# -- A07 projection outbox and search ------------------------------------------------------------

def drain(ops, worker="w1"):
    for claim in ops.claim_projections("search", worker, limit=100):
        ops.apply_search_projection(claim, worker)


def test_search_lags_but_get_is_current_and_projection_is_monotonic(app, ops, gen, scope, publisher):
    bundles = admitted(publisher, scope, title="Invoice 2026")
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
    publisher.publish(scope, admitted(publisher, scope)[0], gen)
    claims = ops.claim_projections("search", "dead-worker", lease_seconds=0)
    assert claims
    time.sleep(0.05)
    retaken = ops.claim_projections("search", "w2")
    assert {c["commit_id"] for c in retaken} >= {c["commit_id"] for c in claims}
    assert ops.apply_search_projection(claims[0], "dead-worker") is False  # lost its lease
    for c in retaken:
        assert ops.apply_search_projection(c, "w2")


def test_failed_projection_backs_off_and_goes_dead(app, ops, gen, scope, publisher, monkeypatch):
    publisher.publish(scope, admitted(publisher, scope)[0], gen)
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
        for b in admitted(publisher, scope, title=title, text=text, tags=tags):
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


def _pages(app, scope, first, **query):
    page, seen = first, list(first["items"])
    while page["next_cursor"]:
        page = app.search(scope, **query, cursor=page["next_cursor"])
        seen += page["items"]
    return seen


def test_search_pagination_pins_the_snapshot(app, ops, gen, scope, publisher):
    for n in range(5):
        publisher.publish(scope, admitted(publisher, scope, title=f"Doc {n}")[0], gen)
    drain(ops)
    query = dict(q="doc", tag=None, status=None, limit=2)
    first = app.search(scope, **query)
    publisher.publish(scope, admitted(publisher, scope, title="Doc late")[0], gen)
    drain(ops)  # projected after the snapshot: must not appear on later pages
    seen = [i["asset_id"] for i in _pages(app, scope, first, **query)]
    assert len(seen) == len(set(seen)) == 5


# -- U3: the snapshot survives updates to existing assets ---------------------------------------

@pytest.mark.parametrize("change", ["title", "status", "tag"])
def test_search_snapshot_survives_updates_to_unseen_assets(app, ops, gen, scope, publisher, change):
    chains = []
    for n in range(5):
        bundles = admitted(publisher, scope, title=f"Doc {n}", tags=["keep"])
        publisher.publish(scope, bundles[0], gen)
        chains.append(bundles)
    drain(ops)
    query = dict(q="doc", tag="keep" if change == "tag" else None,
                 status="accepted" if change == "status" else None, limit=2)
    first = app.search(scope, **query)
    snapshot = {i["asset_id"] for i in first["items"]}
    unseen = [b for b in chains if b[0]["registry"]["asset_id"] not in snapshot]
    assert len(unseen) == 3
    for bundles in unseen:  # each unseen member moves to a revision the query no longer matches
        update = deep(bundles[1])  # status accepted -> processing
        if change == "title":
            set_title(update, "Renamed")
        if change == "tag":
            set_title(update, update["registry"]["index_metadata"]["title"], tags=["dropped"])
        publisher.publish(scope, update, gen)
    drain(ops)
    items = _pages(app, scope, first, **query)
    ids = [i["asset_id"] for i in items]
    assert sorted(ids) == sorted(b[0]["registry"]["asset_id"] for b in chains)  # each original member once
    assert len(ids) == len(set(ids))
    for item in items:  # in its original snapshot form
        assert (item["registry_version"], item["status"]) == (1, "accepted")
        assert item["index_metadata"]["title"].startswith("Doc") and item["index_metadata"]["tags"] == ["keep"]
    assert len(app.search(scope, **dict(query, limit=100))["items"]) == 2  # a new search sees the updates


def test_projection_in_flight_at_snapshot_creation_stays_invisible(urls, app, ops, gen, scope, publisher):
    chains = []
    for n in range(4):
        bundles = admitted(publisher, scope, title=f"Doc {n}")
        publisher.publish(scope, bundles[0], gen)
        chains.append(bundles)
    drain(ops)
    target = max(chains, key=lambda b: b[0]["registry"]["asset_id"])  # sorts last: lands on page 2
    publisher.publish(scope, set_title(deep(target[1]), "Renamed"), gen)
    query = dict(q="doc", tag=None, status=None, limit=2)
    pages = {}

    def barrier(point):  # the projection has written but not committed when page 1 is taken
        if point == "search_projection:before_commit" and "first" not in pages:
            pages["first"] = app.search(scope, **query)

    projector = CoordinatorRepository(urls["ops"], cursor_secret=SECRET, fault=barrier)
    for claim in projector.claim_projections("search", "wp", limit=100):
        projector.apply_search_projection(claim, "wp")  # commits after page 1's snapshot
    items = _pages(app, scope, pages["first"], **query)
    by_id = {i["asset_id"]: i for i in items}
    assert len(items) == len(by_id) == 4
    assert by_id[target[0]["registry"]["asset_id"]]["registry_version"] == 1
    assert target[0]["registry"]["asset_id"] not in {
        i["asset_id"] for i in app.search(scope, **dict(query, limit=100))["items"]}  # renamed since


def test_pruning_keeps_versions_for_live_cursors_and_rejects_pruned_snapshots(app, ops, gen, scope, publisher):
    chains = []
    for n in range(3):
        bundles = admitted(publisher, scope, title=f"Doc {n}")
        publisher.publish(scope, bundles[0], gen)
        chains.append(bundles)
    drain(ops)
    query = dict(q="doc", tag=None, status=None, limit=1)
    first = app.search(scope, **query)
    for bundles in chains:
        publisher.publish(scope, set_title(deep(bundles[1]), "Renamed"), gen)
    drain(ops)
    assert ops.prune_search_versions() == 0  # default lifetime: every live cursor is protected
    assert len(_pages(app, scope, first, **query)) == 3
    # with a zero lifetime, the mark this run records already covers the superseding transactions
    assert ops.prune_search_versions(ttl_seconds=0, margin_seconds=0) == 3
    assert code(lambda: app.search(scope, **query, cursor=first["next_cursor"])) == "INVALID_REQUEST"
    assert app.search(scope, **dict(query, limit=100))["items"] == []  # current versions no longer match


def test_cursors_do_not_survive_recovery(app, ops, drive, gen, scope, publisher):
    for n in range(3):
        publisher.publish(scope, admitted(publisher, scope, title=f"C {n}")[0], gen)
    drain(ops)
    cursor = app.search(scope, q="c", tag=None, status=None, limit=1)["next_cursor"]
    Reconciler(ops, drive).run(ops.enter_recovery())
    assert code(lambda: app.search(scope, q="c", tag=None, status=None, limit=1, cursor=cursor)) \
        == "INVALID_REQUEST"


def test_bad_cursors_are_invalid_requests(app, ops, gen, scope, publisher, monkeypatch):
    for n in range(3):
        publisher.publish(scope, admitted(publisher, scope, title=f"C {n}")[0], gen)
    drain(ops)
    cursor = app.search(scope, q="c", tag=None, status=None, limit=1)["next_cursor"]
    tampered = cursor[:-2] + ("A" if cursor[-2] != "A" else "B") + cursor[-1]
    for bad, kwargs in ((tampered, {"q": "c"}), (cursor, {"q": "other"}),
                        (cursor, {"q": "c", "tag": "x"})):
        assert code(lambda: app.search(scope, tag=kwargs.get("tag"), status=None, limit=1, cursor=bad,
                                       q=kwargs["q"])) == "INVALID_REQUEST"
    other_scope = contract.Scope(scope.tenant_id, "workspace-2")
    with pytest.raises(contract.PipelineFailure):
        app.search(other_scope, q="c", tag=None, status=None, limit=1, cursor=cursor)
    monkeypatch.setattr(repository_module, "CURSOR_TTL_SECONDS", -1)
    expired = app.search(scope, q="c", tag=None, status=None, limit=1)["next_cursor"]
    with pytest.raises(contract.PipelineFailure):
        app.search(scope, q="c", tag=None, status=None, limit=1, cursor=expired)


# -- A17 scope isolation, A16 reuse --------------------------------------------------------------

def test_other_scopes_cannot_see_or_reuse(app, ops, gen, scope, publisher, urls):
    bundles = admitted(publisher, scope, title="Private")
    for b in bundles:
        publisher.publish(scope, b, gen, processing_fingerprint=FINGERPRINT)
    drain(ops)
    asset = bundles[0]["registry"]["asset_id"]
    sha = bundles[2]["registry"]["identity"]["sha256"]
    for other in (contract.Scope(scope.tenant_id + "-x", scope.workspace_id),
                  contract.Scope(scope.tenant_id, "workspace-x")):
        assert code(lambda: app.get(other, asset)) == "NOT_FOUND"
        assert app.search(other, q="private", tag=None, status=None)["items"] == []
        assert app.find_reusable(other, sha, FINGERPRINT) is None
        assert app.list_events(other, asset, 0, 10) == []
        with pytest.raises(contract.PipelineFailure):
            app.get_job(other, bundles[0]["registry"]["ingestion_id"])
        assert code(lambda: ops.get(other, asset)) == "NOT_FOUND"  # explicit predicates, even with BYPASSRLS
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
    "DELETE FROM paios_ingest.quarantined_assets",
])
def test_runtime_roles_cannot_rewrite_history(urls, gen, scope, publisher, sql):
    publisher.publish(scope, admitted(publisher, scope)[0], gen)
    for role in ("app", "ops"):
        with psycopg.connect(urls[role]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)


def test_append_only_trigger_also_stops_the_owner(urls, gen, scope, publisher):
    publisher.publish(scope, admitted(publisher, scope)[0], gen)
    with psycopg.connect(urls["admin"]) as conn:  # superuser: trigger still fires unless disabled
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM paios_ingest.audit_events")


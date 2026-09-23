"""Checkpoint interface and authenticated in-memory fixture; no live backend.

Checkpoints are verification evidence, never canonical asset records. The fixture
key represents an independently controlled signer; production key custody and a
separately administered retention backend are still deployment decisions.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Protocol

from ..errors import failure
from .canonical import canonical_bytes


class CheckpointStore(Protocol):
    def append(self, data: bytes) -> None: ...
    def latest(self) -> bytes | None: ...


def sign_checkpoint(*, sequence: int, created_at: str, heads: list[dict], key: bytes) -> bytes:
    if len(key) < 32:
        raise ValueError('fixture signing key must contain at least 32 bytes')
    body = canonical_bytes(dict(format='paios.checkpoint/1', contract_release='1.0.0',
                                sequence=sequence, created_at=created_at, heads=heads))
    return canonical_bytes(dict(manifest=body.decode(), sha256=hashlib.sha256(body).hexdigest(),
                                signature=hmac.new(key, body, hashlib.sha256).hexdigest()))


def verify_checkpoint(data: bytes, *, key: bytes, history: dict) -> list[tuple]:
    """history maps (tenant, workspace, asset, version) to verified marker hashes.
    A newer recovered head is valid only if its anchored predecessor still matches.
    """
    try:
        outer = json.loads(data)
        body = outer['manifest'].encode()
        if (hashlib.sha256(body).hexdigest() != outer['sha256'] or not hmac.compare_digest(
                hmac.new(key, body, hashlib.sha256).hexdigest(), outer['signature'])):
            raise ValueError('authentication')
        manifest = json.loads(body)
        if manifest['format'] != 'paios.checkpoint/1' or manifest['contract_release'] != '1.0.0':
            raise ValueError('format')
        missing = []
        for head in manifest['heads']:
            identity = (head['tenant_id'], head['workspace_id'], head['asset_id'], head['registry_version'])
            if history.get(identity) != head['marker_sha256']:
                missing.append(identity)
        return missing
    except (ValueError, KeyError, TypeError, AttributeError):
        raise failure('INTEGRITY_FAILED', 'commit', 'Invalid checkpoint evidence') from None


class FakeCheckpointStore:
    FIXTURE_ONLY = True

    def __init__(self):
        self._records: list[bytes] = []

    def append(self, data: bytes) -> None:
        sequence = json.loads(json.loads(data)['manifest'])['sequence']
        if sequence != len(self._records) + 1:
            raise ValueError('checkpoints append in sequence without replacement')
        self._records.append(bytes(data))

    def latest(self) -> bytes | None:
        return self._records[-1] if self._records else None


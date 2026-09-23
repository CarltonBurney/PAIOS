import json

import pytest

from paios_ingestion import contract
from paios_ingestion.persistence.checkpoints import FakeCheckpointStore, sign_checkpoint, verify_checkpoint


def test_checkpoint_anchors_history_not_only_latest_head():
    key = b'x' * 32
    head = dict(tenant_id='t', workspace_id='w', asset_id='a', registry_version=2, marker_sha256='a'*64)
    checkpoint = sign_checkpoint(sequence=1, created_at='2026-09-23T00:00:00Z', heads=[head], key=key)
    store = FakeCheckpointStore()
    store.append(checkpoint)
    history = {('t', 'w', 'a', 2): 'a'*64, ('t', 'w', 'a', 3): 'b'*64}
    assert verify_checkpoint(store.latest(), key=key, history=history) == []
    assert verify_checkpoint(checkpoint, key=key, history={}) == [('t', 'w', 'a', 2)]
    with pytest.raises(ValueError):
        store.append(checkpoint)
    forged = json.loads(checkpoint)
    forged['signature'] = '0'*64
    with pytest.raises(contract.PipelineFailure):
        verify_checkpoint(json.dumps(forged).encode(), key=key, history=history)


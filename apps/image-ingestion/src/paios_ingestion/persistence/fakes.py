"""In-memory Google Drive (DGE) and Microsoft Graph (OneDrive) fakes with fault injection.

Fixture-only: no network, no credentials. They model the provider behaviours the
Packet 2 decisions depend on, so adapters can later be tested against them:

- Drive: files are created with pre-generated IDs (retrying the same ID with the
  same bytes is safe; different bytes conflict); `sha256Checksum` is reported;
  content restrictions exist but are mutable, so they do not make files immutable.
- Graph: items have historical versions; hashes expose `quickXorHash` only (no
  SHA-256, as Graph documents); a pinned version can be pruned; content can change
  between upload and verification.
- Faults: `fail_before` (nothing written), `lost_response` (written, then the
  caller sees a timeout), both per operation and consumed in order.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import uuid
from collections import defaultdict, deque


class TransientError(Exception):
    """Retryable provider failure (timeout, 5xx, throttling)."""


class ConflictError(Exception):
    """The provider refused because the target already exists with other content."""


class NotFoundError(Exception):
    pass


class _Faults:
    def __init__(self):
        self._queue = defaultdict(deque)

    def inject(self, operation: str, *kinds: str) -> None:
        self._queue[operation].extend(kinds)

    def next(self, operation: str) -> str | None:
        return self._queue[operation].popleft() if self._queue[operation] else None


def quick_xor_hash(data: bytes) -> str:
    """Microsoft's QuickXorHash (160-bit, shift 11, length folded in), base64."""
    width, value = 160, 0
    for index, byte in enumerate(data):
        offset = (index * 11) % width
        value ^= (byte << offset) & ((1 << width) - 1)
        if offset > width - 8:
            value ^= byte >> (width - offset)
    raw = bytearray(value.to_bytes(20, "little"))
    for i, b in enumerate(len(data).to_bytes(8, "little")):
        raw[12 + i] ^= b
    return base64.b64encode(bytes(raw)).decode("ascii")


class FakeDrive:
    def __init__(self):
        self.faults = _Faults()
        self._reserved: set[str] = set()
        self._files: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def generate_ids(self, count: int) -> list[str]:
        ids = [f"drive-{uuid.uuid4().hex}" for _ in range(count)]
        self._reserved.update(ids)
        return ids

    def create(self, file_id: str, *, name: str, parent: str, data: bytes,
               app_properties: dict | None = None) -> dict:
        self.calls.append(("create", file_id))
        fault = self.faults.next("create")
        if fault == "fail_before":
            raise TransientError("injected failure before write")
        if file_id not in self._reserved:
            raise ValueError("file IDs must be pre-generated")
        existing = self._files.get(file_id)
        if existing is not None:
            if existing["data"] != data:
                raise ConflictError(f"{file_id} already exists with different content")
        else:
            self._files[file_id] = {"id": file_id, "name": name, "parent": parent, "data": bytes(data),
                                    "appProperties": dict(app_properties or {}), "readOnly": False,
                                    "revision": 1}
        if fault == "lost_response":
            raise TransientError("injected timeout after write")
        return self.metadata(file_id)

    def metadata(self, file_id: str) -> dict:
        f = self._files.get(file_id)
        if f is None:
            raise NotFoundError(file_id)
        return {"id": f["id"], "name": f["name"], "parents": [f["parent"]], "size": str(len(f["data"])),
                "sha256Checksum": hashlib.sha256(f["data"]).hexdigest(), "appProperties": dict(f["appProperties"]),
                "contentRestrictions": [{"readOnly": f["readOnly"]}], "headRevisionId": str(f["revision"])}

    def download(self, file_id: str) -> bytes:
        fault = self.faults.next("download")
        if fault == "fail_before":
            raise TransientError("injected download failure")
        if file_id not in self._files:
            raise NotFoundError(file_id)
        return self._files[file_id]["data"]

    def list_children(self, parent: str) -> list[dict]:
        return [self.metadata(i) for i, f in self._files.items() if f["parent"] == parent]

    def set_read_only(self, file_id: str, read_only: bool) -> None:
        self._files[file_id]["readOnly"] = read_only

    def update_content(self, file_id: str, data: bytes) -> None:
        """Any editor can lift the restriction and edit: restrictions are not immutability."""
        f = self._files[file_id]
        if f["readOnly"]:
            raise PermissionError("content restricted; remove the restriction first")
        f["data"], f["revision"] = bytes(data), f["revision"] + 1

    def delete(self, file_id: str) -> None:
        self._files.pop(file_id, None)


class FakeGraph:
    def __init__(self, drive_id: str = "fake-onedrive"):
        self.drive_id = drive_id
        self.faults = _Faults()
        self._items: dict[str, dict] = {}
        self._paths: dict[str, str] = {}
        self._ids = itertools.count(1)

    def upload(self, path: str, data: bytes, *, conflict_behavior: str = "fail") -> dict:
        fault = self.faults.next("upload")
        if fault == "fail_before":
            raise TransientError("injected failure before upload")
        if path in self._paths:
            if conflict_behavior == "fail":
                raise ConflictError(path)
            item = self._items[self._paths[path]]
            self._add_version(item, data)
        else:
            item_id = f"item-{next(self._ids)}"
            item = {"id": item_id, "path": path, "versions": []}
            self._items[item_id], self._paths[path] = item, item_id
            self._add_version(item, data)
        if fault == "lost_response":
            raise TransientError("injected timeout after upload")
        return self.item(item["id"])

    @staticmethod
    def _add_version(item: dict, data: bytes) -> None:
        item["versions"].append({"id": f"{len(item['versions']) + 1}.0", "data": bytes(data)})

    def item(self, item_id: str) -> dict:
        item = self._items.get(item_id)
        if item is None:
            raise NotFoundError(item_id)
        current = item["versions"][-1]
        return {"id": item_id, "name": item["path"].rsplit("/", 1)[-1], "size": len(current["data"]),
                "eTag": f'"{{{item_id}}},{len(item["versions"])}"', "cTag": f'"c:{{{item_id}}},{len(item["versions"])}"',
                "file": {"hashes": {"quickXorHash": quick_xor_hash(current["data"])}},  # no sha256Hash
                "currentVersionId": current["id"]}

    def item_by_path(self, path: str) -> dict:
        if path not in self._paths:
            raise NotFoundError(path)
        return self.item(self._paths[path])

    def versions(self, item_id: str) -> list[str]:
        return [v["id"] for v in self._items[item_id]["versions"]]

    def download(self, item_id: str, version_id: str) -> bytes:
        """Content of a specific historical version; never silently the latest."""
        fault = self.faults.next("download")
        if fault == "fail_before":
            raise TransientError("injected download failure")
        for v in self._items.get(item_id, {}).get("versions", []):
            if v["id"] == version_id:
                return v["data"]
        raise NotFoundError(f"{item_id}@{version_id}")

    def replace_content(self, item_id: str, data: bytes) -> str:
        """Someone else writes a new version (e.g. between upload and verification)."""
        self._add_version(self._items[item_id], data)
        return self._items[item_id]["versions"][-1]["id"]

    def prune_version(self, item_id: str, version_id: str) -> None:
        versions = self._items[item_id]["versions"]
        versions[:] = [v for v in versions if v["id"] != version_id]

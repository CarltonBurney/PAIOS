"""Interface stubs only. JSON values MUST validate against schemas/contracts.schema.json.
No decoder, OCR, HTTP or repository implementation is provided.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Protocol, Sequence

JsonObject = Mapping[str, Any]

@dataclass(frozen=True)
class Scope:
    tenant_id: str
    workspace_id: str

@dataclass(frozen=True)
class Limits:
    max_source_bytes: int
    max_pages: int
    max_pixels_per_page: int
    max_total_pixels: int
    pdf_dpi: int
    deadline_utc: str

@dataclass(frozen=True)
class LocalPage:
    """Temporary normalized RGB PNG; never serialize path into persistent objects."""
    page_number: int
    path: Path
    width: int
    height: int
    sha256: str
    render_dpi: int | None

@dataclass(frozen=True)
class DecodeResult:
    pages: Sequence[LocalPage]
    metadata: JsonObject  # Metadata schema
    decoder_id: str
    decoder_version: str
    original_format: str

class MediaTypeDetector(Protocol):
    def detect(self, stream: BinaryIO, filename_hint: str) -> str:
        """Return verified MIME; restore stream position; raise PipelineFailure on failure."""
        ...

class MediaDecoder(Protocol):
    decoder_id: str
    decoder_version: str
    def supports(self, detected_mime_type: str) -> bool: ...
    def decode(self, source: Path, detected_mime_type: str, output_dir: Path,
               limits: Limits) -> DecodeResult: ...

class DecoderRegistry(Protocol):
    def resolve(self, detected_mime_type: str) -> MediaDecoder: ...

class HashService(Protocol):
    def sha256(self, source: Path) -> str: ...
    def perceptual_hash(self, page: LocalPage) -> JsonObject | None:
        """Return algorithm/version/value; advisory only."""
        ...

class MetadataExtractor(Protocol):
    def extract(self, source: Path, detected_mime_type: str,
                allow_gps: bool) -> JsonObject: ...  # Metadata

class OCRProvider(Protocol):
    provider_id: str
    provider_version: str
    def recognize(self, *, asset_id: str, scope: Scope, pages: Sequence[LocalPage],
                  options: JsonObject, result_id: str, deadline_utc: str) -> JsonObject:
        """Return OCRResult. Caller supplies stable ID and validated options/profile."""
        ...

class SourceConnector(Protocol):
    def fetch(self, *, scope: Scope, source: JsonObject, destination: Path,
              limits: Limits) -> JsonObject:
        """Fetch authorized exact source version; return Source; detect version change."""
        ...

class WorkingAssetStore(Protocol):
    def put_verified(self, *, scope: Scope, local_path: Path, sha256: str,
                     operation_id: str) -> JsonObject:
        """Idempotent OneDrive persistence; return StorageRef only after verification."""
        ...

class AssetRepository(Protocol):
    def get(self, scope: Scope, asset_id: str) -> JsonObject: ...  # RegistryEntry
    def find_reusable(self, scope: Scope, original_sha256: str,
                      processing_fingerprint: str) -> JsonObject | None: ...

@dataclass(frozen=True)
class Reservation:
    ingestion_id: str
    asset_id: str
    request_digest: str
    replay: bool

@dataclass(frozen=True)
class CommitReceipt:
    commit_id: str
    registry_version: int
    canonical_manifest_id: str
    manifest_sha256: str
    committed_at: str

class IngestionRepository(Protocol):
    def reserve(self, *, scope: Scope, idempotency_key: str,
                request_digest: str, request: JsonObject) -> Reservation: ...
    def commit(self, *, bundle: JsonObject, expected_version: int) -> CommitReceipt:
        """CommitBundle; publish Registry/Audit/Index atomically in logical visibility."""
        ...
    def lookup_commit(self, scope: Scope, commit_id: str) -> CommitReceipt | None: ...
    def get_job(self, scope: Scope, ingestion_id: str) -> JsonObject: ...  # Job

class AuditRepository(Protocol):
    def list_events(self, scope: Scope, asset_id: str,
                    after_version: int, limit: int) -> Sequence[JsonObject]: ...
    # No standalone public write/delete: all appends go through commit(bundle).

class IndexRepository(Protocol):
    def search(self, scope: Scope, *, q: str | None, tag: str | None,
               status: str | None, limit: int, cursor: str | None) -> JsonObject: ...
    def apply_committed(self, receipt: CommitReceipt, row: JsonObject) -> None:
        """Monotonic, idempotent search projection update; never create canonical truth."""
        ...

class PipelineFailure(Exception):
    """Adapter exception contract: .error is a validated Error, with sanitized details."""
    error: JsonObject

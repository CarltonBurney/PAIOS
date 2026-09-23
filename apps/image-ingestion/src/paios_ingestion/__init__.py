"""PAIOS image ingestion, Packet 1: pre-OCR foundation (contract release 1.0.0)."""
from .cache import WorkCache
from .detect import SignatureMediaTypeDetector
from .foundation import FoundationResult, IngestionFoundation, new_asset_id
from .hashing import ContentHashService
from .metadata import OriginalMetadataExtractor
from .registry import PriorityDecoderRegistry, default_registry

__all__ = [
    "ContentHashService", "FoundationResult", "IngestionFoundation", "OriginalMetadataExtractor",
    "PriorityDecoderRegistry", "SignatureMediaTypeDetector", "WorkCache", "default_registry",
    "new_asset_id",
]

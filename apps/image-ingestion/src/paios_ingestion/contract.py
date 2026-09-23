"""Binding to the issued contract package (contracts/image-ingestion, release 1.0.0).

The interface dataclasses/protocols and the JSON Schema are loaded from the
contract package itself so there is exactly one definition of each shape.
Set PAIOS_CONTRACTS_DIR to point at a different copy of the package.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

CONTRACT_RELEASE = "1.0.0"


def _locate() -> Path:
    override = os.environ.get("PAIOS_CONTRACTS_DIR")
    if override:
        return Path(override).resolve()
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts" / "image-ingestion"
        if (candidate / "interfaces" / "contracts.py").is_file():
            return candidate
    raise RuntimeError("contracts/image-ingestion not found; set PAIOS_CONTRACTS_DIR")


CONTRACTS_DIR = _locate()


def _load_interfaces():
    name = "paios_contracts_interfaces_v1"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CONTRACTS_DIR / "interfaces" / "contracts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


interfaces = _load_interfaces()

Scope = interfaces.Scope
Limits = interfaces.Limits
LocalPage = interfaces.LocalPage
DecodeResult = interfaces.DecodeResult
PipelineFailure = interfaces.PipelineFailure
MediaTypeDetector = interfaces.MediaTypeDetector
MediaDecoder = interfaces.MediaDecoder
DecoderRegistry = interfaces.DecoderRegistry
HashService = interfaces.HashService
MetadataExtractor = interfaces.MetadataExtractor
Reservation = interfaces.Reservation
CommitReceipt = interfaces.CommitReceipt


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    schema = json.loads((CONTRACTS_DIR / "schemas" / "contracts.schema.json").read_text(encoding="utf-8"))
    if not schema.get("$id", "").endswith(CONTRACT_RELEASE):
        raise RuntimeError(f"contract package is not release {CONTRACT_RELEASE}: {schema.get('$id')}")
    return schema


@lru_cache(maxsize=None)
def _validator(definition: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/$defs/{definition}", "$defs": _schema()["$defs"]},
        format_checker=FormatChecker(),
    )


def validate(definition: str, value: Any) -> None:
    """Raise jsonschema.ValidationError unless value matches $defs/<definition>."""
    _validator(definition).validate(value)


def subschema(definition: str) -> dict[str, Any]:
    return _schema()["$defs"][definition]


def test_limits(deadline_utc: str) -> Limits:
    """Issued development/test limit profile (PACKET-1-ISSUED-CONTRACT.md)."""
    return Limits(
        max_source_bytes=100 * 1024 * 1024,
        max_pages=200,
        max_pixels_per_page=50_000_000,
        max_total_pixels=200_000_000,
        pdf_dpi=300,
        deadline_utc=deadline_utc,
    )

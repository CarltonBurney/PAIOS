"""Supervised decode: run a decoder in a child process that is killed at the deadline.

Between-page deadline checks cannot interrupt one long native decoder call
(libheif, PDFium, libjpeg). The child process is the cancellation boundary:
when the deadline passes it is killed, its partial output is removed, and the
attempt fails with LIMIT_EXCEEDED. A child that dies without replying (native
crash, OOM kill) fails with DECODE_FAILED instead of hanging or crashing the
caller.
"""
from __future__ import annotations

import dataclasses
import multiprocessing
from datetime import datetime, timezone
from pathlib import Path

from . import contract
from .errors import failure
from .normalize import page_allocator, parse_utc

_GRACE_SECONDS = 5  # time for a child that has replied to exit normally


def _child(decoder, source: str, mime: str, output_dir: str, limits: dict, cache, conn) -> None:
    token = page_allocator.set(cache.allocate if cache is not None else None)
    try:
        result = decoder.decode(Path(source), mime, Path(output_dir), contract.Limits(**limits))
        conn.send(("ok", {
            "pages": [dataclasses.asdict(p) | {"path": str(p.path)} for p in result.pages],
            "metadata": result.metadata, "decoder_id": result.decoder_id,
            "decoder_version": result.decoder_version, "original_format": result.original_format,
        }))
    except contract.PipelineFailure as exc:
        conn.send(("failure", exc.error))
    except BaseException as exc:  # never let the child die silently
        conn.send(("error", type(exc).__name__))
    finally:
        page_allocator.reset(token)
        conn.close()


def _discard(output_dir: Path) -> None:
    for path in output_dir.glob("page-*.png"):
        path.unlink(missing_ok=True)


def supervised_decode(decoder: contract.MediaDecoder, source: Path, mime: str, output_dir: Path,
                      limits: contract.Limits, cache=None) -> contract.DecodeResult:
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child, daemon=True,
        args=(decoder, str(source), mime, str(output_dir), dataclasses.asdict(limits), cache, sender))
    process.start()
    sender.close()
    remaining = (parse_utc(limits.deadline_utc) - datetime.now(timezone.utc)).total_seconds()
    try:
        message = receiver.recv() if receiver.poll(max(remaining, 0)) else None
    except EOFError:
        message = ("crashed", None)
    finally:
        receiver.close()
    if message is None:
        process.kill()
        process.join()
        _discard(output_dir)
        raise failure("LIMIT_EXCEEDED", "decode", "Decoding did not finish before the deadline",
                      details=[("deadline_utc", "decoder process stopped at the deadline")])
    process.join(_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join()
    kind, payload = message
    if kind == "ok":
        pages = [contract.LocalPage(**(page | {"path": Path(page["path"])})) for page in payload["pages"]]
        return contract.DecodeResult(pages=pages, metadata=payload["metadata"],
                                     decoder_id=payload["decoder_id"],
                                     decoder_version=payload["decoder_version"],
                                     original_format=payload["original_format"])
    _discard(output_dir)
    if kind == "failure":
        exc = contract.PipelineFailure(f"{payload['code']}: {payload['message']}")
        exc.error = payload
        raise exc
    if kind == "crashed":
        raise failure("DECODE_FAILED", "decode", "Decoder process exited unexpectedly",
                      details=[("exit_code", str(process.exitcode))])
    raise failure("INTERNAL_ERROR", "decode", "Decoder process failed", details=[("error", payload)])

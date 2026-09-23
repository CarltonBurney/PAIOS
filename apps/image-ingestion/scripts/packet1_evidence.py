"""Print Packet 1 fixture evidence as JSON: hashes, geometry, decoder versions.

Fixtures are synthetic and regenerated on each run (the PDF embeds a creation
date), so original hashes differ between runs; geometry and statuses do not.
Run from apps/image-ingestion: python scripts/packet1_evidence.py
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "tests")]
import fixtures as fx  # noqa: E402
from paios_ingestion import IngestionFoundation, WorkCache, contract  # noqa: E402

FIXTURES = [(fx.jpeg, "orientation6.jpg"), (fx.png_rgba, "alpha.png"), (fx.heic, "orientation6.heic"),
            (fx.avif, "orientation6.avif"), (fx.pdf, "two-page.pdf"), (fx.tiff_pages, "three-page.tif")]


def main() -> None:
    work = Path(tempfile.mkdtemp())
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    foundation = IngestionFoundation(WorkCache(work / "cache"))
    evidence = []
    for build, name in FIXTURES:
        result = foundation.prepare(build(work / name), name, contract.test_limits(deadline))
        entry = result.to_dict()
        entry["fixture"] = name
        entry["pages"] = [{"page_number": p.page_number, "width": p.width, "height": p.height,
                           "render_dpi": p.render_dpi, "sha256": p.sha256}
                          for p in (result.decode.pages if result.decode else [])]
        evidence.append(entry)
    print(json.dumps({"contract_release": contract.CONTRACT_RELEASE, "fixtures": evidence}, indent=2))


if __name__ == "__main__":
    main()

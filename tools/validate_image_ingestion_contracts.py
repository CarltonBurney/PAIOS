"""Read-only preflight for the immutable image-ingestion contract release.

Run from the repository: python tools/validate_image_ingestion_contracts.py
Optional: --report /path/outside/the/package/report.json
The original 1.0.0 validator is executed against an isolated copy because it
writes validation-report.json. Neither the release nor its manifest is edited.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path,
                        default=Path(__file__).resolve().parents[1]/'contracts/image-ingestion')
    parser.add_argument('--report', type=Path, help='Optional report outside the contract package')
    args = parser.parse_args()
    package = args.package.resolve()
    report_path = args.report.resolve() if args.report else None
    if report_path and (report_path == package or package in report_path.parents):
        parser.error('--report must be outside the immutable contract package')
    if not (package/'acceptance/validate_contracts.py').is_file():
        parser.error('contract package does not contain acceptance/validate_contracts.py')

    with tempfile.TemporaryDirectory(prefix='paios-contract-preflight-') as temporary:
        isolated = Path(temporary)/'contracts'
        shutil.copytree(package, isolated, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        # Discard a copied historical report, so only this run can produce success evidence.
        generated = isolated/'validation-report.json'
        generated.unlink(missing_ok=True)
        run = subprocess.run(
            [sys.executable, str(isolated/'acceptance/validate_contracts.py')],
            cwd=isolated, capture_output=True, text=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        if run.returncode:
            sys.stdout.write(run.stdout)
            sys.stderr.write(run.stderr)
            return run.returncode
        if not generated.is_file():
            sys.stderr.write('Validator returned without producing fresh evidence.\n')
            return 1
        report = json.loads(generated.read_text(encoding='utf-8'))
        if report.get('status') != 'passed':
            sys.stderr.write('Contract validation did not pass.\n')
            return 1

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks_passed': report['checks_passed'],
                      'contract_package_modified': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

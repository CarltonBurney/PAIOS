from __future__ import annotations

import sys
from pathlib import Path

# The app is a standalone package inside apps/; make it importable without an
# install step so the suite runs from a bare checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

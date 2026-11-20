"""Enhetstester för `gemensamt` och grundläggande CLI/testsvit."""

from __future__ import annotations

import sys
from pathlib import Path
from pkgutil import extend_path

_SRC_MAPP = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_MAPP) not in sys.path:
    sys.path.insert(0, str(_SRC_MAPP))

__path__ = extend_path(__path__, __name__)

"""Where the harness finds its files.

In a source checkout everything lives at the repo root (rules/, runs/, .env),
as it always has. Installed from a wheel there is no repo root, so runs/ and
.env come from the current directory and the rulebook defaults to the bundled
example. LTL_HARNESS_RULEBOOK and LTL_HARNESS_RUNS override either way.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
_REPO = PKG_DIR.parents[1]
IN_CHECKOUT = (_REPO / "rules" / "refund.rules.yaml").is_file()
BASE_DIR = _REPO if IN_CHECKOUT else Path.cwd()

UI_DIR = PKG_DIR / "ui"
EXAMPLE_RULEBOOK = PKG_DIR / "examples" / "refund.rules.yaml"
RULEBOOK_PATH = Path(
    os.environ.get("LTL_HARNESS_RULEBOOK")
    or (BASE_DIR / "rules" / "refund.rules.yaml" if IN_CHECKOUT else EXAMPLE_RULEBOOK)
)
RUNS_DIR = Path(os.environ.get("LTL_HARNESS_RUNS") or BASE_DIR / "runs")
ENV_FILE = BASE_DIR / ".env"


def read_dotenv() -> dict[str, str] | None:
    """KEY=value pairs from .env, or None when there is no .env."""
    if not ENV_FILE.is_file():
        return None
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out

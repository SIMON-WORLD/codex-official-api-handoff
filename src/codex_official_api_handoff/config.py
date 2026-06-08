from __future__ import annotations

import re
from pathlib import Path


MODEL_PROVIDER_RE = re.compile(r'^\s*model_provider\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def read_model_provider(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    text = config_path.read_text(encoding="utf-8", errors="replace")
    match = MODEL_PROVIDER_RE.search(text)
    if not match:
        return None
    return match.group(1)


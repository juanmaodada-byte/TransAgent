#!/usr/bin/env python3
"""Machine-readable document runtime health check."""

from __future__ import annotations

import json

from transagent.backend.pipeline.document_quality import check_document_runtime_health


def main() -> int:
    health = check_document_runtime_health(require_cjk_font=True)
    print(json.dumps(health, ensure_ascii=False, sort_keys=True))
    return 0 if health.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

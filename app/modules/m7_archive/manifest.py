from datetime import datetime, timezone

from app.codec import digest
from app.config import RULESET


def create(source, result, elapsed_ms, parent=None, revision=1):
    source_sha256 = digest(source)
    basis = {"source_sha256": source_sha256, "ruleset": RULESET}
    if parent is not None:
        basis["parent"] = parent
    return {"id": digest(basis), "batch": source["batch"], "at": source["at"],
            "ruleset": RULESET, "source_sha256": source_sha256, "digest": digest(result),
            "parent": parent, "revision": revision,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_ms": elapsed_ms, "parameters": {"as_of": source["at"]},
            "input_counts": {key: len(value) for key, value in source.items() if isinstance(value, list)}}

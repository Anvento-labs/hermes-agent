"""BSON → JSON-safe serialization helpers."""

from __future__ import annotations

import json
from typing import Any, List


def _serialize_doc(doc: Any) -> Any:
    from bson import json_util

    return json.loads(json_util.dumps(doc))


def _serialize_docs(docs: List[Any]) -> List[Any]:
    return [_serialize_doc(doc) for doc in docs]

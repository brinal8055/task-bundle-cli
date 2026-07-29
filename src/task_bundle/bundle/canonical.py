import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json_bytes(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    data: Any
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json", exclude_none=False)
    else:
        data = value
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"

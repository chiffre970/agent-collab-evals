"""Write-once local evidence for the operator pilot command."""

import os
import tempfile
from pathlib import Path

from .canonical import canonical_json_bytes, digest_bytes


def retain_bytes(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".pilot-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise RuntimeError("pilot evidence already exists with different content") from None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return digest_bytes(content)


def retain_document(path: Path, document: object) -> str:
    return retain_bytes(path, canonical_json_bytes(document))

"""OPC relationship parsing and safe target resolution."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from .namespaces import PKG_REL, qn
from .xml_parser import parse_bytes


@dataclass(frozen=True)
class Relationship:
    rel_id: str
    rel_type: str
    target: str
    external: bool
    part_name: str | None  # resolved internal part name, None when external or unsafe
    unsafe: bool = False   # internal target escaping the package root

    @property
    def type_name(self) -> str:
        return self.rel_type.rsplit("/", 1)[-1]


def rels_part_for(part_name: str) -> str:
    """``word/document.xml`` -> ``word/_rels/document.xml.rels``."""
    directory, name = posixpath.split(part_name)
    return posixpath.join(directory, "_rels", f"{name}.rels")


def resolve_target(source_part: str, target: str) -> str | None:
    """Resolve an internal relationship target; return None if it escapes the package."""
    if target.startswith("/"):
        resolved = posixpath.normpath(target.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
    if resolved.startswith("../") or resolved == ".." or resolved.startswith("/"):
        return None
    return resolved


def parse_relationships(data: bytes, source_part: str) -> dict[str, Relationship]:
    root = parse_bytes(data)
    rels: dict[str, Relationship] = {}
    for el in root.iter(qn(PKG_REL, "Relationship")):
        rel_id = el.get("Id", "")
        target = el.get("Target", "")
        external = el.get("TargetMode", "Internal") == "External"
        part_name = None if external else resolve_target(source_part, target)
        rels[rel_id] = Relationship(
            rel_id=rel_id,
            rel_type=el.get("Type", ""),
            target=target,
            external=external,
            part_name=part_name,
            unsafe=(not external and part_name is None),
        )
    return rels

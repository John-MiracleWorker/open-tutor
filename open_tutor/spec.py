"""Data model + (de)serialization for the grounded-curriculum spec (SPEC §6).

A CurriculumSpec is the subject-agnostic artifact the Designer produces and the
Learner Engine consumes. Swapping this YAML = a new subject.
"""
from __future__ import annotations

import dataclasses
import ntpath
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

_IDENTIFIER_MAX_LEN = 128
_WINDOWS_RESERVED_CHARS = set('<>:"|?*')


def validate_identifier(value: str, *, field_name: str = "identifier") -> str:
    """Validate a value that may become a spec/cache filename component.

    IDs are intentionally not normalized: changing a supplied ID could create
    collisions.  Callers get a clear error instead.  The conservative
    component policy preserves the repository's stable ``snake_case`` and
    hyphenated IDs while rejecting traversal, absolute paths, controls, and
    platform-specific filename hazards before any filesystem operation.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(value) > _IDENTIFIER_MAX_LEN:
        raise ValueError(f"{field_name} is too long (maximum {_IDENTIFIER_MAX_LEN} characters)")
    if value in {".", ".."}:
        raise ValueError(f"{field_name} cannot be a dot path segment")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field_name} contains a control character")
    if "/" in value or "\\" in value:
        raise ValueError(f"{field_name} cannot contain path separators")
    if os.path.isabs(value) or ntpath.isabs(value) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{field_name} cannot be an absolute path")
    if any(char in _WINDOWS_RESERVED_CHARS for char in value):
        raise ValueError(f"{field_name} contains a filename-reserved character")
    if value.endswith((".", " ")):
        raise ValueError(f"{field_name} cannot end with a dot or space")
    return value


@dataclass
class CorpusSource:
    id: str
    name: str
    url: str
    tier: int = 2
    status: str = "unknown"          # live | dead | unknown (set by verifier)
    http_status: Optional[int] = None
    covers: Optional[List[str]] = None   # node ids whose keywords were found on this page

    def __post_init__(self) -> None:
        validate_identifier(self.id, field_name="corpus id")


@dataclass
class Misconception:
    id: str
    text: str

    def __post_init__(self) -> None:
        validate_identifier(self.id, field_name="misconception id")


@dataclass
class Node:
    id: str
    title: str
    defn: str
    prereqs: List[str] = field(default_factory=list)
    misconceptions: List[Misconception] = field(default_factory=list)
    grounding_corpus: List[str] = field(default_factory=list)  # corpus ids
    oracle: Optional[str] = None            # oracle name (T3) or None
    covers_keywords: List[str] = field(default_factory=list)   # for source-coverage check
    # ---- verification (populated by the verifier) ----
    status: str = "unknown"                 # grounded | thin | unverified
    verification: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id, field_name="node id")
        for prereq in self.prereqs:
            validate_identifier(prereq, field_name="prerequisite id")
        for source_id in self.grounding_corpus:
            validate_identifier(source_id, field_name="grounding corpus id")
        if self.oracle is not None:
            validate_identifier(self.oracle, field_name="oracle id")


@dataclass
class CurriculumSpec:
    subject: str
    title: str
    scope: Dict[str, Any] = field(default_factory=dict)
    tiers: Dict[str, Any] = field(default_factory=dict)
    corpus: List[CorpusSource] = field(default_factory=list)
    oracle: Dict[str, Any] = field(default_factory=dict)
    nodes: List[Node] = field(default_factory=list)
    generator_note: str = ""

    def __post_init__(self) -> None:
        validate_identifier(self.subject, field_name="subject")
        corpus_ids = [source.id for source in self.corpus]
        duplicates = sorted({source_id for source_id in corpus_ids
                             if corpus_ids.count(source_id) > 1})
        if duplicates:
            raise ValueError("duplicate corpus id(s): " + ", ".join(duplicates))
        node_ids = [node.id for node in self.nodes]
        duplicate_nodes = sorted({node_id for node_id in node_ids
                                  if node_ids.count(node_id) > 1})
        if duplicate_nodes:
            raise ValueError("duplicate node id(s): " + ", ".join(duplicate_nodes))

    # ---------- helpers ----------
    def node(self, nid: str) -> Optional[Node]:
        for n in self.nodes:
            if n.id == nid:
                return n
        return None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CurriculumSpec":
        if not isinstance(d, dict):
            raise TypeError("curriculum spec must be a mapping")
        d = dict(d)
        d["corpus"] = [CorpusSource(**dict(c)) for c in d.get("corpus", [])]
        nodes = []
        for n in d.get("nodes", []):
            n = dict(n)
            # YAML bundles historically used the schema spelling ``def``;
            # the Python dataclass retains ``defn`` for a valid identifier.
            if "def" in n and "defn" not in n:
                n["defn"] = n.pop("def")
            n["misconceptions"] = [Misconception(**dict(m))
                                    for m in n.get("misconceptions", [])]
            nodes.append(Node(**n))
        d["nodes"] = nodes
        return cls(**d)


def save_yaml(spec: CurriculumSpec, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(spec.to_yaml())
    print(f"[spec] wrote {path}")


def load_yaml(path: str) -> CurriculumSpec:
    with open(path, "r", encoding="utf-8") as f:
        return CurriculumSpec.from_dict(yaml.safe_load(f))

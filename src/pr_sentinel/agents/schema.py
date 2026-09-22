"""The JSON schema every specialist is constrained to.

Written by hand rather than generated from the Pydantic model: the API requires
`additionalProperties: false` and an exhaustive `required` on every object, and a
generated schema drags in `$defs`/`$ref` indirection that buys nothing here. One
explicit schema is easier to keep correct than a generator plus its workarounds.
"""

from __future__ import annotations

from ..domain.enums import Category, Severity

_EVIDENCE = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["diff", "chunk", "convention"]},
        "file_path": {"type": "string"},
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "excerpt": {"type": "string"},
    },
    "required": ["kind", "file_path", "line_start", "line_end", "excerpt"],
    "additionalProperties": False,
}

_FINDING = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "Path exactly as it appears in the diff."},
        "line_start": {"type": "integer", "description": "New-file line number from the @@ header."},
        "line_end": {"type": "integer"},
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "severity": {"type": "string", "enum": [s.value for s in Severity]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "title": {"type": "string", "description": "One line, under 80 characters."},
        "body": {"type": "string", "description": "What is wrong and what to do about it."},
        "rationale": {
            "type": "string",
            "description": "The specific observation that makes the claim true.",
        },
        "evidence": {"type": "array", "items": _EVIDENCE},
    },
    "required": [
        "file_path",
        "line_start",
        "line_end",
        "category",
        "severity",
        "confidence",
        "title",
        "body",
        "rationale",
        "evidence",
    ],
    "additionalProperties": False,
}

AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two sentences. Empty findings is fine."},
        "findings": {"type": "array", "items": _FINDING},
    },
    "required": ["summary", "findings"],
    "additionalProperties": False,
}

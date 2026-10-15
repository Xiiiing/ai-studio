"""Versioned role instructions; no model assignment, execution or cache ownership.

PromptText remains a JSON-serializable str so legacy content-addressed cache keys
are identical. Metadata is carried explicitly across renderer transformations,
never inferred from a role name or retroactively written into historical calls.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, SchemaError

from .creative_evidence_io import read_json, read_bytes, digest

PROMPT_ROOT = Path(__file__).parent / "prompts"
_ROLE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_VERSION = re.compile(r"[1-9][0-9]*\.[0-9]+\.[0-9]+\Z")


@dataclass(frozen=True)
class PromptSpec:
    role: str
    version: str
    responsibility: str
    input_schema_json: str
    output_schema_refs: tuple[str, ...]
    template_sha256: str
    contract_sha256: str


class PromptText(str):
    """String-compatible text plus immutable, explicitly selected provenance."""
    def __new__(cls, text, spec, transforms=()):
        instance = str.__new__(cls, text)
        object.__setattr__(instance, "spec", spec)
        object.__setattr__(instance, "transforms", tuple(transforms))
        return instance

    def __setattr__(self, name, value):
        raise AttributeError("Prompt identity is immutable; derive a new prompt")

    def __reduce_ex__(self, protocol):
        return type(self), (str(self), self.spec, self.transforms)


def derive_prompt(source, rendered, transformation):
    """Preserve provenance only for explicit, named renderer operations."""
    if not isinstance(source, PromptText):
        return rendered
    if not isinstance(transformation, str) or not _ROLE.fullmatch(transformation):
        raise ValueError("Invalid prompt transformation")
    return PromptText(rendered, source.spec,
                      tuple(dict.fromkeys((*source.transforms, transformation))))


def load_prompt(role, version=None, *, root=None):
    """Read a reviewed local catalog. Custom roots are for offline experiments.

    Templates are checked on each load (no stale global directory cache). A
    running process retains its loaded immutable templates until code reload;
    changing the catalog does not mutate any existing project or approved media.
    """
    if not isinstance(role, str) or not _ROLE.fullmatch(role):
        raise ValueError("Invalid prompt role")
    if version is not None and (not isinstance(version, str) or not _VERSION.fullmatch(version)):
        raise ValueError("Invalid prompt version")
    root = Path(root) if root is not None else PROMPT_ROOT
    catalog = read_json(root / "catalog.json", maximum=256 * 1024)
    if set(catalog) != {"schema", "defaults", "prompts"} or catalog["schema"] != "ai-studio.prompts.v1":
        raise ValueError("Unsupported prompt catalog")
    if not isinstance(catalog["defaults"], dict) or not isinstance(catalog["prompts"], list):
        raise ValueError("Invalid prompt catalog shape")
    version = version or catalog["defaults"].get(role)
    matches = [item for item in catalog["prompts"] if isinstance(item, dict)
               and item.get("role") == role and item.get("version") == version]
    if len(matches) != 1 or not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("Unknown or duplicate prompt role/version")
    item = matches[0]
    required = {"role", "version", "responsibility", "path", "sha256", "input_schema", "output_schema_refs"}
    if set(item) != required or item["path"] != f"{role}/{version}.txt":
        raise ValueError("Invalid prompt contract/path")
    if not isinstance(item["responsibility"], str) or not item["responsibility"].strip():
        raise ValueError("Prompt responsibility is required")
    references = item["output_schema_refs"]
    if (not isinstance(references, list) or not references
            or any(not isinstance(ref, str) or not re.fullmatch(r"factory\.[a-z_]+:[A-Za-z_]+", ref)
                   for ref in references)):
        raise ValueError("Prompt output schema references are required")
    inputs = item["input_schema"]
    if (not isinstance(inputs, dict) or inputs.get("type") != "object"
            or inputs.get("additionalProperties") is not False):
        raise ValueError("Prompt input context must be a closed object schema")
    pending = [inputs]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if {"$ref", "$dynamicRef", "$recursiveRef"}.intersection(node):
                raise ValueError("Prompt context schemas must be self-contained; references are disabled")
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    try:
        Draft202012Validator.check_schema(inputs)
    except SchemaError as exc:
        raise ValueError("Invalid prompt input context schema") from exc
    raw = read_bytes(root / item["path"], maximum=64 * 1024)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != item["sha256"]:
        raise ValueError("Prompt template checksum mismatch")
    text = raw.decode("utf-8", errors="strict")
    if not text.strip() or "\x00" in text:
        raise ValueError("Invalid prompt text")
    spec = PromptSpec(role, version, item["responsibility"],
                      json.dumps(inputs, ensure_ascii=False, sort_keys=True),
                      tuple(references), actual, digest(item))
    return PromptText(text, spec)


def prompt_receipt(system, role, payload, schema, *, wire_system=None):
    """Validate role/context and return hashes, not private prompt/payload text."""
    if not isinstance(system, str):
        raise ValueError("Prompt must be a string")
    value = {"schema": "ai-studio.prompt-receipt.v1", "status": "unregistered",
             "role": role, "version": None, "template_sha256": None,
             "contract_sha256": None, "transforms": [],
             "rendered_sha256": hashlib.sha256(system.encode()).hexdigest(),
             "input_sha256": digest(payload), "output_schema_sha256": digest(schema),
             "wire_system_sha256": hashlib.sha256(wire_system.encode()).hexdigest() if wire_system is not None else None}
    if isinstance(system, PromptText):
        spec = system.spec
        if spec.role != role:
            raise ValueError("Registered prompt role does not match the calling role")
        if list(Draft202012Validator(json.loads(spec.input_schema_json)).iter_errors(payload)):
            # Do not include the invalid value (potential private data) in errors.
            raise ValueError("Payload exceeds the registered prompt input context")
        value.update(status="registered", version=spec.version,
                     template_sha256=spec.template_sha256, contract_sha256=spec.contract_sha256,
                     transforms=list(system.transforms))
    return value

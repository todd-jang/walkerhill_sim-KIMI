from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any


_FORMAT_BY_SUFFIX = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".md": "markdown",
    ".txt": "text",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".html": "html",
    ".htm": "html",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".pdf": "pdf",
    ".gz": "gzip",
    ".zip": "zip",
}
_TABLE_FORMATS = {"csv", "tsv"}
_JSON_FORMATS = {"json", "jsonl"}
_DOCUMENT_FORMATS = {"markdown", "text", "html", "yaml"}
_CODE_FORMATS = {"python", "javascript", "typescript"}
_MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".mov",
    ".mp3", ".wav", ".woff", ".woff2", ".ttf", ".otf",
}
_EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


def _format_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _MEDIA_SUFFIXES:
        return "media"
    return _FORMAT_BY_SUFFIX.get(suffix, "other")


def _domain_for(path: Path, artifact_format: str) -> str:
    lowered = path.as_posix().lower()
    if artifact_format in _CODE_FORMATS:
        return "source_code"
    if artifact_format == "media":
        return "media"
    if artifact_format in {"gzip", "zip"}:
        return "archive"
    if artifact_format in _DOCUMENT_FORMATS:
        return "documentation"
    if "evidence_chain" in lowered or "evidence-chain" in lowered:
        return "evidence_chain"
    if "instagram" in lowered or "observed_payment" in lowered:
        return "behavioral_evidence"
    if "ontology" in lowered:
        return "ontology"
    if any(token in lowered for token in ("experiment", "ab_test", "hypoth", "heldout")):
        return "experiment"
    if any(token in lowered for token in ("journey", "guest-state", "guest_state")):
        return "journey"
    if any(token in lowered for token in ("menu", "dining", "program_capacity", "facility")):
        return "dining_and_capacity"
    if any(token in lowered for token in ("payment", "revenue", "adr", "revpar")):
        return "revenue_reference"
    if any(token in lowered for token in ("scenario", "simulation", "sim_result", "sensitivity")):
        return "scenario"
    if any(token in lowered for token in ("persona", "profile", "blog")):
        return "persona_evidence"
    if any(token in lowered for token in ("occupancy", "capacity", "room", "date_factor", "type_mix", "purpose_mix")):
        return "demand_reference"
    return "other"


def _classify_scalar(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return "null"
    if normalized.lower() in {"true", "false"}:
        return "boolean"
    try:
        int(normalized)
        return "integer"
    except ValueError:
        pass
    try:
        float(normalized)
        return "number"
    except ValueError:
        pass
    try:
        date.fromisoformat(normalized)
        return "date"
    except ValueError:
        pass
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return "datetime"
    except ValueError:
        return "string"


def _merge_scalar_types(types: set[str]) -> str:
    types.discard("null")
    if not types:
        return "null"
    if types <= {"integer", "number"}:
        return "number" if "number" in types else "integer"
    if len(types) == 1:
        return next(iter(types))
    return "mixed"


def _merge_json_shapes(shapes: list[dict[str, Any]]) -> dict[str, Any]:
    if not shapes:
        return {"type": "unknown"}
    value_types = sorted({shape.get("type", "unknown") for shape in shapes})
    if len(value_types) != 1:
        return {"type": "mixed", "value_types": value_types}

    value_type = value_types[0]
    if value_type == "object":
        mapped_objects = [
            shape for shape in shapes
            if isinstance(shape.get("additional_properties"), dict)
        ]
        if mapped_objects:
            return {
                "type": "object",
                "map_key_count_max": max(
                    shape.get("map_key_count", 0) for shape in mapped_objects
                ),
                "key_examples": sorted({
                    key
                    for shape in mapped_objects
                    for key in shape.get("key_examples", [])
                })[:5],
                "additional_properties": _merge_json_shapes([
                    shape["additional_properties"] for shape in mapped_objects
                ]),
                "truncated": True,
            }
        property_sets = [
            shape.get("properties", {})
            for shape in shapes
            if isinstance(shape.get("properties", {}), dict)
        ]
        property_names = sorted({
            name for properties in property_sets for name in properties
        })
        truncated = any(shape.get("truncated", False) for shape in shapes)
        truncated = truncated or len(property_names) > 200
        property_names = property_names[:200]
        properties = {
            name: _merge_json_shapes([
                source[name] for source in property_sets if name in source
            ])
            for name in property_names
        }
        optional = [
            name for name in property_names
            if len([source for source in property_sets if name in source]) < len(shapes)
        ]
        return {
            "type": "object",
            "properties": properties,
            "optional_properties": optional,
            "truncated": truncated,
        }
    if value_type == "array":
        item_shapes = [
            shape["items"] for shape in shapes
            if isinstance(shape.get("items"), dict)
        ]
        return {
            "type": "array",
            "items": _merge_json_shapes(item_shapes),
        }
    return {"type": value_type}


def _extract_table(path: Path, artifact_format: str) -> dict[str, Any]:
    delimiter = "\t" if artifact_format == "tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        if reader.fieldnames is None:
            raise ValueError("table has no header row")
        fieldnames = reader.fieldnames
        types: dict[str, set[str]] = {name: set() for name in fieldnames}
        nullable: dict[str, bool] = {name: False for name in fieldnames}
        row_count = 0
        for row in reader:
            row_count += 1
            for name in fieldnames:
                value = row.get(name)
                scalar_type = _classify_scalar(value or "")
                types[name].add(scalar_type)
                nullable[name] = nullable[name] or scalar_type == "null"
    return {
        "kind": "tabular",
        "delimiter": "tab" if artifact_format == "tsv" else "comma",
        "row_count": row_count,
        "columns": [
            {
                "name": name,
                "inferred_type": _merge_scalar_types(types[name]),
                "nullable": nullable[name],
            }
            for name in fieldnames
        ],
    }


def _json_shape(value: Any, depth: int = 0) -> dict[str, Any]:
    if isinstance(value, dict):
        if depth >= 5:
            return {"type": "object", "truncated": True}
        keys = sorted(value)
        if len(keys) > 20:
            sample_keys = keys[:20]
            return {
                "type": "object",
                "map_key_count": len(keys),
                "key_examples": keys[:5],
                "additional_properties": _merge_json_shapes([
                    _json_shape(value[key], depth + 1)
                    for key in sample_keys
                ]),
                "sampled_value_count": len(sample_keys),
                "truncated": True,
            }
        return {
            "type": "object",
            "properties": {
                key: _json_shape(value[key], depth + 1)
                for key in keys
            },
            "truncated": False,
        }
    if isinstance(value, list):
        item_shapes = [_json_shape(item, depth + 1) for item in value[:50]]
        return {
            "type": "array",
            "items": _merge_json_shapes(item_shapes),
            "item_count": len(value),
            "sampled_item_count": min(len(value), 50),
        }
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    return {"type": "unknown"}


def _extract_json(path: Path, artifact_format: str) -> dict[str, Any]:
    if artifact_format == "json":
        with path.open("r", encoding="utf-8-sig") as stream:
            value = json.load(stream)
        return {"kind": "json", "shape": _json_shape(value)}

    top_level_types: Counter[str] = Counter()
    top_level_shapes: list[dict[str, Any]] = []
    object_keys: set[str] = set()
    line_count = 0
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at line {line_number}: {error.msg}") from error
            line_count += 1
            shape = _json_shape(value)
            top_level_types[shape["type"]] += 1
            if len(top_level_shapes) < 50:
                top_level_shapes.append(shape)
            if isinstance(value, dict):
                object_keys.update(value)
    return {
        "kind": "jsonl",
        "record_count": line_count,
        "top_level_type_counts": dict(sorted(top_level_types.items())),
        "record_shape": _merge_json_shapes(top_level_shapes),
        "record_shape_sampled_count": len(top_level_shapes),
        "object_keys": sorted(object_keys)[:500],
        "object_keys_truncated": len(object_keys) > 500,
    }


def _extract_document(path: Path, artifact_format: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    headings = [
        line.lstrip("#").strip()
        for line in text.splitlines()
        if line.startswith("#")
    ]
    return {
        "kind": "document_metadata",
        "character_count": len(text),
        "line_count": len(text.splitlines()),
        "headings": headings[:200],
        "headings_truncated": len(headings) > 200,
        "format": artifact_format,
    }


def _repository_files(root: Path) -> tuple[list[Path], str | None, bool]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        paths = [
            root / relative
            for relative in result.stdout.decode("utf-8", errors="strict").split("\0")
            if relative
        ]
        revision_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        revision = revision_result.stdout.strip() if revision_result.returncode == 0 else None
        return paths, revision, True

    paths = []
    for current_root, dirs, filenames in os.walk(root, followlinks=False):
        dirs[:] = sorted(directory for directory in dirs if directory not in _EXCLUDED_DIRS)
        paths.extend(Path(current_root) / filename for filename in filenames)
    return sorted(paths), None, False


def build_hillink_contract(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Hillink repository directory does not exist: {root}")

    paths, revision, git_tracked = _repository_files(root)
    artifacts: list[dict[str, Any]] = []
    format_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    extraction_status_counts: Counter[str] = Counter()
    missing_count = 0

    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        artifact_format = _format_for(path)
        domain = _domain_for(Path(relative), artifact_format)
        format_counts[artifact_format] += 1
        domain_counts[domain] += 1
        if path.is_symlink():
            link_target = os.readlink(path).encode("utf-8")
            extraction_status_counts["parse_error"] += 1
            artifacts.append({
                "path": relative,
                "size_bytes": len(link_target),
                "sha256": hashlib.sha256(link_target).hexdigest(),
                "format": artifact_format,
                "domain": domain,
                "extraction_status": "parse_error",
                "content_contract": None,
                "error": "symbolic link target was recorded, not followed",
            })
            continue
        if not path.is_file():
            missing_count += 1
            extraction_status_counts["parse_error"] += 1
            artifacts.append({
                "path": relative,
                "size_bytes": None,
                "sha256": None,
                "format": artifact_format,
                "domain": domain,
                "extraction_status": "parse_error",
                "content_contract": None,
                "error": "tracked file is missing from the working tree",
            })
            continue

        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)

        content_contract: dict[str, Any] | None = None
        extraction_status = "opaque"
        error_message: str | None = None
        try:
            if artifact_format in _TABLE_FORMATS:
                content_contract = _extract_table(path, artifact_format)
                extraction_status = "schema_extracted"
            elif artifact_format in _JSON_FORMATS:
                content_contract = _extract_json(path, artifact_format)
                extraction_status = "schema_extracted"
            elif artifact_format in _DOCUMENT_FORMATS:
                content_contract = _extract_document(path, artifact_format)
                extraction_status = "metadata_only"
            elif artifact_format in _CODE_FORMATS:
                content_contract = {"kind": "source_file"}
                extraction_status = "metadata_only"
        except (OSError, UnicodeError, csv.Error, json.JSONDecodeError, ValueError) as error:
            extraction_status = "parse_error"
            error_message = str(error)

        extraction_status_counts[extraction_status] += 1
        artifacts.append({
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": digest.hexdigest(),
            "format": artifact_format,
            "domain": domain,
            "extraction_status": extraction_status,
            "content_contract": content_contract,
            "error": error_message,
        })

    return {
        "contract_version": "1.0.0",
        "source": {
            "repository": "https://github.com/BK8909/Hillink",
            "revision": revision,
        },
        "coverage": {
            "repository_file_count": len(paths),
            "indexed_file_count": len(artifacts),
            "total_size_bytes": sum(
                artifact["size_bytes"] or 0 for artifact in artifacts
            ),
            "format_counts": dict(sorted(format_counts.items())),
            "domain_counts": dict(sorted(domain_counts.items())),
            "extraction_status_counts": dict(sorted(extraction_status_counts.items())),
            "inventory_complete": (
                git_tracked
                and len(artifacts) == len(paths)
                and missing_count == 0
            ),
        },
        "artifacts": artifacts,
    }


def build_stable_prompt_context(contract: dict[str, Any]) -> str:
    """Return a compact, stable source-contract prefix; do not include raw customer rows."""
    structured_count = sum(
        artifact["format"] in _TABLE_FORMATS | _JSON_FORMATS
        for artifact in contract["artifacts"]
    )
    context = {
        "source": contract["source"],
        "contract_version": contract["contract_version"],
        "coverage": contract["coverage"],
        "structured_artifact_count": structured_count,
        "evidence_rules": [
            "Treat repository records as source evidence with path-level provenance.",
            "Do not treat simulated, inferred, or synthetic records as observed hotel bookings.",
            "Do not claim causal effects, representativeness, or validation unless the source supports it.",
            "State missing fields and extraction limitations instead of filling them by assumption.",
            "Retrieve relevant records and their source paths locally; this compact context is not the full corpus.",
        ],
    }
    return json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract a provenance-preserving JSON contract from a local Hillink checkout."
    )
    parser.add_argument("repository", help="Path to a local Hillink repository checkout")
    parser.add_argument("--output", help="Write the contract JSON to this path (default: stdout)")
    args = parser.parse_args()
    contract = build_hillink_contract(args.repository)
    serialized = json.dumps(contract, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()

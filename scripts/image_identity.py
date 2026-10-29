"""Bind image identity to actual packaged source, rather than revision metadata alone."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_digest(root):
    root = Path(root)
    entries = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".ses"}:
            continue
        if path.is_symlink():
            raise ValueError(f"Source symlinks are not supported: {path}")
        if path.is_file():
            entries[path.relative_to(root).as_posix()] = file_digest(path)
    if not entries:
        raise ValueError(f"No source files found in {root}")
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_identity(root, revision, harness_revision=None):
    root = Path(root)
    if revision != "unversioned" and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Image revision must be a full commit SHA")
    harness_revision = harness_revision or revision
    if harness_revision != "unversioned" and not re.fullmatch(r"[0-9a-f]{40}", harness_revision):
        raise ValueError("Harness revision must be a full commit SHA")
    parts = {name: source_digest(root/name) for name in ("api", "core", "db", "rag_target", "scripts", "tests")}
    parts["requirements.lock"] = file_digest(root/"requirements.lock")
    parts["alembic.ini"] = file_digest(root/"alembic.ini")
    return {"schema_version": 1, "revision_sha": revision, "harness_revision_sha": harness_revision,
        "target_source_sha256": parts["rag_target"],
        "application_source_sha256": hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest(),
        "dependency_lock_sha256": parts["requirements.lock"]}


def read_identity(root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    path = root/"build-info.json"
    expected = json.loads(path.read_text()) if path.exists() else build_identity(root, "unversioned")
    actual = build_identity(root, expected["revision_sha"], expected.get("harness_revision_sha"))
    if expected != actual:
        raise ValueError("Packaged source changed after image identity was generated")
    return {**actual, "source_verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", default="unversioned")
    parser.add_argument("--harness-revision")
    parser.add_argument("--expected-target-sha", default="")
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    if args.write:
        result = build_identity(args.root, args.revision, args.harness_revision)
        if args.expected_target_sha and result["target_source_sha256"] != args.expected_target_sha:
            raise ValueError("Copied RAG source does not match the requested Git tree")
        args.write.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
    else:
        print(json.dumps(read_identity(args.root), sort_keys=True))


if __name__ == "__main__": main()

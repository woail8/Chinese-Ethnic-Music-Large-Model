from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_store import build_rag_index, load_rag_index, save_rag_index


BASE_DIR = Path(__file__).resolve().parent
REFS_DIR = BASE_DIR / "references"
INDEX_PATH = BASE_DIR / "rag_index.json"


def cmd_build(_: argparse.Namespace) -> int:
    index = build_rag_index(REFS_DIR)
    save_rag_index(index, INDEX_PATH)
    print(f"OK: built {len(index.chunks)} chunks from {len(index.files)} files")
    print(f"Index: {INDEX_PATH}")
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    if not INDEX_PATH.exists():
        print("Index not found. Run: py rag_cli.py build")
        return 1
    index = load_rag_index(INDEX_PATH)
    print(json.dumps({"files": len(index.files), "chunks": len(index.chunks)}, ensure_ascii=False))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if not INDEX_PATH.exists():
        print("Index not found. Run: py rag_cli.py build")
        return 1
    index = load_rag_index(INDEX_PATH)
    hits = index.search(args.query, top_k=args.top_k)
    for ch, score in hits:
        print(f"[{score:.2f}] {ch.source}#{ch.chunk_id}")
        print(ch.text)
        print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="rag_cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Build or rebuild rag_index.json from references/")
    p_build.set_defaults(func=cmd_build)

    p_stats = sub.add_parser("stats", help="Show index stats")
    p_stats.set_defaults(func=cmd_stats)

    p_search = sub.add_parser("search", help="Search in index")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=4)
    p_search.set_defaults(func=cmd_search)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())


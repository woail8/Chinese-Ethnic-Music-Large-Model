from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path


_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    t = text.lower()
    words = _WORD_RE.findall(t)
    cjk_chars = _CJK_RE.findall(t)
    cjk_bigrams: list[str] = []
    if len(cjk_chars) >= 2:
        for i in range(len(cjk_chars) - 1):
            cjk_bigrams.append(cjk_chars[i] + cjk_chars[i + 1])
    return words + cjk_bigrams


def _iter_text_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    files: list[Path] = []
    for ext in (".txt", ".md"):
        files.extend(root.rglob(f"*{ext}"))
    return [p for p in files if p.is_file()]


def _chunk_text(text: str, max_chars: int = 900) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if not buf:
            buf = p
        elif len(buf) + 2 + len(p) <= max_chars:
            buf = buf + "\n\n" + p
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    split_chunks: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            split_chunks.append(c)
            continue
        for i in range(0, len(c), max_chars):
            part = c[i : i + max_chars].strip()
            if part:
                split_chunks.append(part)
    return split_chunks


@dataclass(frozen=True)
class RagChunk:
    source: str
    chunk_id: int
    text: str
    tokens: list[str]


@dataclass(frozen=True)
class RagIndex:
    root_dir: str
    files: list[tuple[str, int]]
    chunks: list[RagChunk]
    df: dict[str, int]
    idf: dict[str, float]
    avgdl: float

    def is_stale(self) -> bool:
        root = Path(self.root_dir)
        current = _iter_text_files(root)
        sig = sorted((str(p.relative_to(root)), int(p.stat().st_mtime)) for p in current)
        return sig != sorted(self.files)

    def search(self, query: str, top_k: int = 4) -> list[tuple[RagChunk, float]]:
        q_tokens = _tokenize(query)
        if not q_tokens or not self.chunks:
            return []

        qset = set(q_tokens)
        k1 = 1.2
        b = 0.75
        avgdl = self.avgdl or 1.0

        results: list[tuple[RagChunk, float]] = []
        for ch in self.chunks:
            tf: dict[str, int] = {}
            for t in ch.tokens:
                if t in qset:
                    tf[t] = tf.get(t, 0) + 1
            if not tf:
                continue

            dl = len(ch.tokens) or 1
            denom_base = k1 * (1.0 - b + b * (dl / avgdl))
            score = 0.0
            for t, f in tf.items():
                idf = self.idf.get(t, 0.0)
                score += idf * (f * (k1 + 1.0)) / (f + denom_base)
            results.append((ch, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


def build_rag_index(root_dir: Path) -> RagIndex:
    files = _iter_text_files(root_dir)
    file_sig = sorted((str(p.relative_to(root_dir)), int(p.stat().st_mtime)) for p in files)

    chunks: list[RagChunk] = []
    df: dict[str, int] = {}
    doclens: list[int] = []

    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = str(fp.relative_to(root_dir))
        for idx, ch in enumerate(_chunk_text(text)):
            tokens = _tokenize(ch)
            if not tokens:
                continue
            chunks.append(RagChunk(source=rel, chunk_id=idx, text=ch, tokens=tokens))
            doclens.append(len(tokens))
            for tok in set(tokens):
                df[tok] = df.get(tok, 0) + 1

    avgdl = (sum(doclens) / len(doclens)) if doclens else 0.0
    n = len(chunks)
    idf: dict[str, float] = {}
    if n:
        for tok, dfi in df.items():
            idf[tok] = math.log(1.0 + (n - dfi + 0.5) / (dfi + 0.5))

    return RagIndex(
        root_dir=str(root_dir),
        files=file_sig,
        chunks=chunks,
        df=df,
        idf=idf,
        avgdl=avgdl,
    )


def save_rag_index(index: RagIndex, path: Path) -> None:
    payload = {
        "root_dir": index.root_dir,
        "files": index.files,
        "chunks": [asdict(c) for c in index.chunks],
        "df": index.df,
        "idf": index.idf,
        "avgdl": index.avgdl,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_rag_index(path: Path) -> RagIndex:
    data = json.loads(path.read_text(encoding="utf-8"))
    chunks = [RagChunk(**c) for c in data.get("chunks", [])]
    return RagIndex(
        root_dir=data["root_dir"],
        files=[tuple(x) for x in data.get("files", [])],
        chunks=chunks,
        df=data.get("df", {}),
        idf={k: float(v) for k, v in data.get("idf", {}).items()},
        avgdl=float(data.get("avgdl", 0.0)),
    )


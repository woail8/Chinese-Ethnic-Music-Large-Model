from __future__ import annotations

import math
import re
from dataclasses import dataclass
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


def _chunk_text(text: str, max_chars: int = 800) -> list[str]:
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
class RefChunk:
    source: str
    chunk_id: int
    text: str
    tokens: list[str]


class ReferenceStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self._chunks: list[RefChunk] = []
        self._df: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._avgdl: float = 0.0
        self._mtime_sig: tuple[tuple[str, int], ...] = ()

    def refresh_if_needed(self) -> None:
        files = _iter_text_files(self.root_dir)
        sig = tuple(sorted((str(p.relative_to(self.root_dir)), int(p.stat().st_mtime)) for p in files))
        if sig == self._mtime_sig:
            return
        self._mtime_sig = sig
        self._rebuild(files)

    def _rebuild(self, files: list[Path]) -> None:
        chunks: list[RefChunk] = []
        df: dict[str, int] = {}
        doclens: list[int] = []
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = str(fp.relative_to(self.root_dir))
            for idx, ch in enumerate(_chunk_text(text)):
                tokens = _tokenize(ch)
                if not tokens:
                    continue
                chunks.append(RefChunk(source=rel, chunk_id=idx, text=ch, tokens=tokens))
                doclens.append(len(tokens))
                seen = set(tokens)
                for tok in seen:
                    df[tok] = df.get(tok, 0) + 1
        self._chunks = chunks
        self._df = df
        self._avgdl = (sum(doclens) / len(doclens)) if doclens else 0.0
        n = len(chunks)
        idf: dict[str, float] = {}
        if n:
            for tok, dfi in df.items():
                idf[tok] = math.log(1.0 + (n - dfi + 0.5) / (dfi + 0.5))
        self._idf = idf

    def search(self, query: str, top_k: int = 4) -> list[tuple[RefChunk, float]]:
        self.refresh_if_needed()
        q_tokens = _tokenize(query)
        if not q_tokens or not self._chunks:
            return []
        qtf: dict[str, int] = {}
        for t in q_tokens:
            qtf[t] = qtf.get(t, 0) + 1

        k1 = 1.2
        b = 0.75
        results: list[tuple[RefChunk, float]] = []
        for ch in self._chunks:
            tf: dict[str, int] = {}
            for t in ch.tokens:
                if t in qtf:
                    tf[t] = tf.get(t, 0) + 1
            if not tf:
                continue
            dl = len(ch.tokens) or 1
            denom_base = k1 * (1.0 - b + b * (dl / (self._avgdl or 1.0)))
            score = 0.0
            for t, f in tf.items():
                idf = self._idf.get(t, 0.0)
                score += idf * (f * (k1 + 1.0)) / (f + denom_base)
            results.append((ch, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


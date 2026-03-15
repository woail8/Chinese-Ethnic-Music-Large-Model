from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    t = (text or "").lower()
    words = _WORD_RE.findall(t)
    cjk_chars = _CJK_RE.findall(t)
    cjk_bigrams: list[str] = []
    if len(cjk_chars) >= 2:
        for i in range(len(cjk_chars) - 1):
            cjk_bigrams.append(cjk_chars[i] + cjk_chars[i + 1])
    return words + cjk_bigrams


def _cjk_count(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


def _choose_encoding(path: Path) -> str:
    try:
        sample = path.read_bytes()[:8192]
    except Exception:
        return "utf-8"

    try:
        t_utf8 = sample.decode("utf-8")
    except UnicodeDecodeError:
        return "gb18030"

    try:
        t_gbk = sample.decode("gb18030", errors="ignore")
    except Exception:
        return "utf-8"

    return "gb18030" if _cjk_count(t_gbk) > _cjk_count(t_utf8) else "utf-8-sig"


@dataclass(frozen=True)
class Triple:
    row_id: int
    head: str
    rel: str
    tail: str
    tokens: list[str]


class KnowledgeGraph:
    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path
        self.triples: list[Triple] = []
        self._inv: dict[str, list[int]] = {}
        self._entities: dict[str, list[int]] = {}
        self._mtime: int = 0

    def refresh_if_needed(self) -> None:
        try:
            mtime = int(self.csv_path.stat().st_mtime)
        except FileNotFoundError:
            self.triples = []
            self._inv = {}
            self._entities = {}
            self._mtime = 0
            return
        if mtime == self._mtime and self.triples:
            return
        self._mtime = mtime
        self._load()

    def _load(self) -> None:
        triples: list[Triple] = []
        inv: dict[str, list[int]] = {}
        entities: dict[str, list[int]] = {}

        encoding = _choose_encoding(self.csv_path)
        with self.csv_path.open("r", encoding=encoding, errors="ignore", newline="") as f:
            reader = csv.reader(f)
            for row_idx, row in enumerate(reader, start=1):
                if not row or len(row) < 3:
                    continue
                h = (row[0] or "").strip()
                r = (row[1] or "").strip()
                t = (row[2] or "").strip()
                if not h or not r or not t:
                    continue
                tokens = _tokenize(h) + _tokenize(r) + _tokenize(t)
                tr = Triple(row_id=row_idx, head=h, rel=r, tail=t, tokens=tokens)
                idx = len(triples)
                triples.append(tr)

                for tok in set(tokens):
                    inv.setdefault(tok, []).append(idx)

                entities.setdefault(h, []).append(idx)
                entities.setdefault(t, []).append(idx)

        self.triples = triples
        self._inv = inv
        self._entities = entities

    def query(self, question: str, *, top_k: int = 10, expand: int = 12) -> list[Triple]:
        self.refresh_if_needed()
        if not self.triples:
            return []

        q = (question or "").strip()
        if not q:
            return []

        q_tokens = _tokenize(q)
        scores: dict[int, float] = {}
        candidates: set[int] = set()

        for tok in set(q_tokens):
            for idx in self._inv.get(tok, []):
                candidates.add(idx)
                scores[idx] = scores.get(idx, 0.0) + 1.0

        matched_entities: set[str] = set()
        if len(q) <= 300:
            for ent in self._entities.keys():
                if len(ent) >= 2 and ent in q:
                    matched_entities.add(ent)

        for ent in matched_entities:
            for idx in self._entities.get(ent, []):
                candidates.add(idx)
                scores[idx] = scores.get(idx, 0.0) + 3.0

        ranked = sorted(candidates, key=lambda i: scores.get(i, 0.0), reverse=True)
        picked: list[int] = ranked[:top_k]

        if expand > 0 and matched_entities:
            extra: list[int] = []
            for ent in list(matched_entities)[:6]:
                extra.extend(self._entities.get(ent, []))
            for i in extra:
                if i not in picked:
                    picked.append(i)
                if len(picked) >= top_k + expand:
                    break

        return [self.triples[i] for i in picked]


def format_triples(triples: list[Triple]) -> str:
    if not triples:
        return ""
    lines: list[str] = []
    for tr in triples:
        lines.append(f"{tr.head} -[{tr.rel}]-> {tr.tail}（来源: knowledge.csv#L{tr.row_id}）")
    return "\n".join(lines)

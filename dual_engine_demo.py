"""
双引擎科教问答演示（RAG + 知识图谱 CSV）

目标：
1）从 references/ 中检索文本资料（RAG）
2）从 knowledge.csv 中匹配相关三元组（轻量知识图谱）
3）把两者合并进同一个 prompt，交给 DeepSeek 回答

依赖：只用本项目自带模块 + requests（见 requirements.txt）
运行方式（Windows PowerShell）：
  py dual_engine_demo.py "侗族大歌有哪些特点？"
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import requests

from kg_store import KnowledgeGraph, format_triples
from rag_store import build_rag_index, load_rag_index, save_rag_index


BASE_DIR = Path(__file__).resolve().parent
API_KEY_PATH = BASE_DIR / "api_key.txt"
PROMPT_PATH = BASE_DIR / "prompt.txt"
REFS_DIR = BASE_DIR / "references"
RAG_INDEX_PATH = BASE_DIR / "rag_index.json"
KG_PATH = BASE_DIR / "knowledge.csv"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip() if path.exists() else ""


def get_api_key() -> str:
    text = read_text(API_KEY_PATH)
    if not text:
        raise RuntimeError("未检测到 API key：请在 api_key.txt 第一行填写 DeepSeek API Key。")
    first = text.splitlines()[0].strip()
    if not first or first == "YOUR_DEEPSEEK_API_KEY_HERE":
        raise RuntimeError("api_key.txt 仍是占位符：请替换为真实 Key。")
    return first


def deepseek_chat(messages: list[dict[str, str]]) -> tuple[str, dict[str, int] | None]:
    url = "https://api.deepseek.com/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    usage_raw = data.get("usage") if isinstance(data, dict) else None
    usage = None
    if isinstance(usage_raw, dict):
        usage = {k: int(v) for k, v in usage_raw.items() if isinstance(v, int)}
    return content, usage


def load_or_build_rag_index() -> Any:
    if RAG_INDEX_PATH.exists():
        idx = load_rag_index(RAG_INDEX_PATH)
        if not idx.is_stale():
            return idx
    idx = build_rag_index(REFS_DIR)
    save_rag_index(idx, RAG_INDEX_PATH)
    return idx


def build_prompt(user_question: str) -> list[dict[str, str]]:
    system_prompt = read_text(PROMPT_PATH) or "你是一个专注民族音乐领域的中文问答助手。"

    guidance = (
        "你将获得两类证据：①参考资料检索摘录（RAG）②知识图谱三元组（CSV）。"
        "请优先基于证据回答；若证据不足覆盖问题，先说明“资料未覆盖/覆盖不足”，再给出通用知识补充。"
        "不要编造资料、人物、年份或出处；不确定就明确说明并给出查证建议。"
        "引用时请标注来源，例如（来源: 文件名#段落号）或（来源: knowledge.csv#L行号）。"
    )

    # 1）RAG：从 references/ 的资料库中检索最相关片段（避免把全量资料都塞进 prompt）
    idx = load_or_build_rag_index()
    hits = idx.search(user_question, top_k=4)
    rag_text = ""
    if hits:
        blocks = [f"[来源: {c.source}#{c.chunk_id} | 相关度: {s:.2f}]\n{c.text}" for c, s in hits]
        rag_text = "\n\n---\n\n".join(blocks)

    # 2）知识图谱：从 knowledge.csv 中匹配相关三元组（实体1,关系,实体2）
    kg = KnowledgeGraph(KG_PATH)
    triples = kg.query(user_question, top_k=10, expand=10)
    kg_text = format_triples(triples)

    sys_content = system_prompt + "\n\n" + guidance
    if rag_text:
        sys_content += "\n\n参考资料摘录（RAG）：\n" + rag_text
    if kg_text:
        sys_content += "\n\n知识图谱（CSV）：\n" + kg_text

    return [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": user_question},
    ]


def main() -> int:
    question = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else ""
    if not question:
        print('用法：py dual_engine_demo.py "你的问题"')
        return 1

    messages = build_prompt(question)
    answer, usage = deepseek_chat(messages)
    print(answer)
    if usage:
        print("\n---")
        print(f"tokens: {usage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

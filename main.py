import os
import uuid
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from rag_store import RagIndex, build_rag_index, load_rag_index, save_rag_index
from session_store import load_session, save_session


BASE_DIR = Path(__file__).resolve().parent
PROMPT_PATH = BASE_DIR / "prompt.txt"
API_KEY_PATH = BASE_DIR / "api_key.txt"
REFS_DIR = BASE_DIR / "references"
INDEX_PATH = BASE_DIR / "rag_index.json"
SESSIONS_DIR = BASE_DIR / "sessions"

_rag_index: RagIndex | None = None


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def get_system_prompt() -> str:
    prompt = read_text_file(PROMPT_PATH)
    if prompt:
        return prompt
    return "你是一个专注民族音乐领域的中文问答助手。仅回答民族音乐相关问题。"


def get_api_key() -> str:
    text = read_text_file(API_KEY_PATH)
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    if not first_line or first_line == "YOUR_DEEPSEEK_API_KEY_HERE":
        return ""
    return first_line


def deepseek_chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> tuple[str, dict[str, int] | None]:
    api_key = get_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail="未检测到 API key，请在 api_key.txt 中填写。")

    url = "https://api.deepseek.com/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=90)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="上游模型服务请求失败，请稍后再试。")

    if resp.status_code >= 400:
        try:
            err = resp.json()
            msg = err.get("error", {}).get("message") or err.get("message") or resp.text
        except Exception:
            msg = resp.text
        raise HTTPException(status_code=502, detail=f"模型服务返回错误：{msg}")

    data = resp.json()
    usage_raw = data.get("usage") if isinstance(data, dict) else None
    usage: dict[str, int] | None = None
    if isinstance(usage_raw, dict):
        pt = usage_raw.get("prompt_tokens")
        ct = usage_raw.get("completion_tokens")
        tt = usage_raw.get("total_tokens")
        if isinstance(pt, int) or isinstance(ct, int) or isinstance(tt, int):
            usage = {}
            if isinstance(pt, int):
                usage["prompt_tokens"] = pt
            if isinstance(ct, int):
                usage["completion_tokens"] = ct
            if isinstance(tt, int):
                usage["total_tokens"] = tt
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not content:
        raise HTTPException(status_code=502, detail="模型服务返回空内容。")
    return content, usage


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list, max_length=40)


class ChatResponse(BaseModel):
    answer: str


class ChatV2Request(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    ref_mode: str = Field(default="rag", pattern="^(rag|full)$")


class ChatV2Response(BaseModel):
    session_id: str
    answer: str
    usage: dict[str, int] | None = None
    summary_usage: dict[str, int] | None = None
    ref_mode: str | None = None


app = FastAPI()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def get_rag_index() -> RagIndex:
    global _rag_index

    auto_build = os.getenv("RAG_AUTO_BUILD", "1").strip() != "0"

    if _rag_index is None:
        if INDEX_PATH.exists():
            _rag_index = load_rag_index(INDEX_PATH)
        elif auto_build:
            idx = build_rag_index(REFS_DIR)
            save_rag_index(idx, INDEX_PATH)
            _rag_index = idx
        else:
            _rag_index = build_rag_index(Path("__nonexistent__"))
        return _rag_index

    if INDEX_PATH.exists():
        try:
            if _rag_index.is_stale() and auto_build:
                idx = build_rag_index(REFS_DIR)
                save_rag_index(idx, INDEX_PATH)
                _rag_index = idx
        except Exception:
            pass
    return _rag_index


def load_full_references(*, max_chars: int) -> str:
    if not REFS_DIR.exists():
        return ""
    files = []
    for ext in (".txt", ".md"):
        files.extend(REFS_DIR.rglob(f"*{ext}"))
    files = [p for p in files if p.is_file()]
    files.sort(key=lambda p: str(p.relative_to(REFS_DIR)))

    blocks: list[str] = []
    total = 0
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not text:
            continue
        rel = str(fp.relative_to(REFS_DIR))
        header = f"[文件: {rel}]"
        chunk = header + "\n" + text
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(chunk) > remaining:
            chunk = chunk[:remaining].rstrip()
        blocks.append(chunk)
        total += len(chunk) + 5
        if total >= max_chars:
            break
    return "\n\n-----\n\n".join(blocks).strip()


def build_system_content(
    system_prompt: str,
    user_query: str,
    *,
    top_k: int = 4,
    ref_mode: str = "rag",
) -> str:
    refs: list[tuple[Any, float]] = []
    full_text = ""
    if ref_mode == "rag":
        idx = get_rag_index()
        refs = idx.search(user_query, top_k=top_k) if user_query else []
    elif ref_mode == "full":
        max_chars = int(os.getenv("FULL_REF_MAX_CHARS", "20000"))
        full_text = load_full_references(max_chars=max_chars)

    guidance = (
        "你将获得参考资料内容（可能是检索摘录或资料库全文）。回答时优先依据参考资料；"
        "若资料不足以覆盖问题，请先明确说明“参考资料未覆盖/覆盖不足”，再给出你基于通用知识的补充回答。"
        "当你引用参考资料中的信息时，请在句末用括号标注来源，如（来源: 文件名#段落号 或 文件名）。"
    )

    sys_content = system_prompt + "\n\n" + guidance
    if ref_mode == "rag" and refs:
        blocks: list[str] = []
        for ch, score in refs:
            blocks.append(f"[来源: {ch.source}#{ch.chunk_id} | 相关度: {score:.2f}]\n{ch.text}")
        joined = "\n\n---\n\n".join(blocks)
        if len(joined) > 3000:
            joined = joined[:3000].rstrip()
        sys_content = sys_content + "\n\n参考资料摘录（检索结果）：\n" + joined
    if ref_mode == "full" and full_text:
        sys_content = sys_content + "\n\n参考资料库（全量，可能截断）：\n" + full_text
    return sys_content


def maybe_summarize(
    summary: str, messages: list[dict[str, str]]
) -> tuple[str, list[dict[str, str]], dict[str, int] | None]:
    if len(messages) <= 16:
        return summary, messages, None

    recent = messages[-8:]
    older = messages[:-8]
    older_text = "\n".join([f'{m["role"]}: {m["content"]}' for m in older])[:6000]
    base = summary.strip()

    sys = (
        "你是会话摘要器。输出一段简洁中文摘要，用于后续对话复用。"
        "只保留与用户目标、已给出的关键信息、已达成结论、未解决问题相关的内容。"
        "不要加入新信息，不要猜测。"
    )
    user = "已有摘要：\n" + (base if base else "（无）") + "\n\n需要归纳的历史对话：\n" + older_text
    new_summary, usage = deepseek_chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.0,
        max_tokens=256,
    )
    return new_summary.strip(), recent, usage


@app.post("/api/chat", response_model=ChatV2Response)
async def chat(req: ChatV2Request) -> ChatV2Response:
    system_prompt = get_system_prompt()
    sid = (req.session_id or "").strip()
    if not sid:
        sid = str(uuid.uuid4())

    session = load_session(SESSIONS_DIR, sid)
    summary = str(session.get("summary", "") or "")
    messages = list(session.get("messages", []) or [])

    user_msg = req.message.strip()
    messages.append({"role": "user", "content": user_msg})

    summary, messages, summary_usage = maybe_summarize(summary, messages)

    ref_mode = (req.ref_mode or "rag").strip()
    sys_content = build_system_content(system_prompt, user_msg, top_k=4, ref_mode=ref_mode)
    if summary.strip():
        sys_content = sys_content + "\n\n会话摘要（供上下文复用）：\n" + summary.strip()

    llm_messages: list[dict[str, str]] = [{"role": "system", "content": sys_content}, *messages[-8:]]
    answer, usage = deepseek_chat(llm_messages, temperature=0.3, max_tokens=1024)

    messages.append({"role": "assistant", "content": answer})
    save_session(
        SESSIONS_DIR,
        sid,
        summary,
        messages[-24:],
        last_prompt=llm_messages,
        last_ref_mode=ref_mode,
        last_usage=usage or {},
        last_summary_usage=summary_usage or {},
    )
    return ChatV2Response(
        session_id=sid,
        answer=answer,
        usage=usage,
        summary_usage=summary_usage,
        ref_mode=ref_mode,
    )


@app.get("/api/prompt")
async def get_prompt(session_id: str) -> dict[str, Any]:
    session = load_session(SESSIONS_DIR, session_id)
    return {
        "session_id": session_id,
        "ref_mode": session.get("last_ref_mode") or "",
        "usage": session.get("last_usage") or {},
        "summary_usage": session.get("last_summary_usage") or {},
        "messages": session.get("last_prompt") or [],
    }


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port_text = os.getenv("PORT", "8002")
    try:
        port = int(port_text)
    except ValueError:
        port = 8002

    uvicorn.run("main:app", host=host, port=port, reload=False)


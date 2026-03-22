import os
import uuid
import hashlib
import json
import threading
import time
import shutil
import subprocess
import tempfile
import base64
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from kg_store import KnowledgeGraph, format_triples
from rag_store import RagIndex, build_rag_index, load_rag_index, save_rag_index
from session_store import load_session, save_session


BASE_DIR = Path(__file__).resolve().parent
PROMPT_PATH = BASE_DIR / "prompt.txt"
API_KEY_PATH = BASE_DIR / "api_key.txt"
MUREKA_API_KEY_PATH = BASE_DIR / "mureka_api_key.txt"
REFS_DIR = BASE_DIR / "references"
KG_PATH = BASE_DIR / "references" / "knowledge" / "knowledge.csv"
if not KG_PATH.exists():
    KG_PATH = BASE_DIR / "knowledge.csv"
INDEX_PATH = BASE_DIR / "rag_index.json"
SESSIONS_DIR = BASE_DIR / "sessions"
MUSIC_JOB_PATH = BASE_DIR / "music_job.json"
MUSIC_LOCK_PATH = BASE_DIR / "music_worker.lock"
MUSIC_QUEUE_PATH = BASE_DIR / "music_queue.json"

_rag_index: RagIndex | None = None
_kg = KnowledgeGraph(KG_PATH)
_music_worker_started = False
_music_lock_handle = None
_music_submit_lock = threading.Lock()
_music_worker_pid: int | None = None
_music_worker_started_at: int | None = None

SIM_DIR = BASE_DIR / "模拟乐器演奏"
SIM_SAMPLES_ROOT = SIM_DIR / "音频文件"
SIM_OUTPUT_DIR = SIM_DIR / "web_outputs"
SIM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))
try:
    import play_midi_with_piano_samples as sim_engine
except Exception:
    sim_engine = None

SCORE_DIR = BASE_DIR / "乐谱转MIDI"
if str(SCORE_DIR) not in sys.path:
    sys.path.insert(0, str(SCORE_DIR))
try:
    import score_to_midi as score_engine
except Exception:
    score_engine = None

OCR_DIR = BASE_DIR / "乐谱数据集生成" / "ocr" / "ocr_segmentation"
if str(OCR_DIR) not in sys.path:
    sys.path.insert(0, str(OCR_DIR))
try:
    from pre_processing import preProcessing as ocr_pre_processing
    from pre_processing import build_sequence_line as ocr_build_sequence_line
except Exception:
    ocr_pre_processing = None
    ocr_build_sequence_line = None

_sim_wav_store: dict[str, str] = {}
_ocr_output_dir = SIM_OUTPUT_DIR / "ocr"
_ocr_upload_dir = _ocr_output_dir / "uploads"
_ocr_web_dir = _ocr_output_dir / "web"
_ocr_score_dir = _ocr_output_dir / "score"
_ocr_upload_dir.mkdir(parents=True, exist_ok=True)
_ocr_web_dir.mkdir(parents=True, exist_ok=True)
_ocr_score_dir.mkdir(parents=True, exist_ok=True)


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


def get_mureka_api_key() -> str:
    text = read_text_file(MUREKA_API_KEY_PATH)
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    if not first_line or first_line == "YOUR_MUREKA_API_KEY_HERE":
        return ""
    return first_line


def get_mureka_base_url() -> str:
    base = os.getenv("MUREKA_BASE_URL", "").strip()
    if base:
        return base.rstrip("/")
    return "https://api.mureka.cn"


_MUREKA_TERMINAL_STATUSES = {"succeeded", "failed", "timeouted", "cancelled"}


def is_mureka_task_active(job: dict[str, Any]) -> bool:
    task_id = str(job.get("task_id") or "").strip()
    if not task_id:
        return False
    status = str(job.get("task_status") or "").strip().lower()
    if not status:
        return True
    return status not in _MUREKA_TERMINAL_STATUSES


def get_mureka_task_hint(job: dict[str, Any]) -> str:
    task_id = str(job.get("task_id") or "").strip()
    status = str(job.get("task_status") or "").strip().lower()
    if task_id:
        return f"task_id={task_id}" + (f"，status={status}" if status else "")
    last_attempt = job.get("last_attempt_at")
    if last_attempt:
        return f"last_attempt_at={last_attempt}"
    return ""

def clear_music_job(*, reason: str) -> dict[str, Any]:
    job = load_music_job()
    cleared_queue_len = clear_music_queue()
    if not job:
        cleared = {
            "cleared_at": int(time.time()),
            "cleared_reason": reason,
            "cleared": True,
            "cleared_queue_len": cleared_queue_len,
        }
        save_music_job(cleared)
        return cleared

    cleared = {
        "cleared_at": int(time.time()),
        "cleared_reason": reason,
        "cleared": True,
        "cleared_queue_len": cleared_queue_len,
        "previous": {
            "job_id": job.get("job_id"),
            "task_id": job.get("task_id"),
            "task_status": job.get("task_status"),
            "trace_id": job.get("trace_id"),
            "model": job.get("model"),
            "submitted_at": job.get("submitted_at"),
            "last_attempt_at": job.get("last_attempt_at"),
            "error": job.get("error"),
        },
    }
    save_music_job(cleared)
    return cleared


def load_music_job() -> dict[str, Any]:
    if not MUSIC_JOB_PATH.exists():
        return {}
    try:
        data = json.loads(MUSIC_JOB_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_music_job(data: dict[str, Any]) -> None:
    data["file_updated_at"] = int(time.time())
    payload = json.dumps(data, ensure_ascii=False)
    last_err: Exception | None = None
    for _ in range(5):
        tmp_name = f"{MUSIC_JOB_PATH.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        tmp = MUSIC_JOB_PATH.with_name(tmp_name)
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, MUSIC_JOB_PATH)
            return
        except PermissionError as e:
            last_err = e
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            time.sleep(0.2)
        except Exception as e:
            last_err = e
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            break
    if last_err:
        raise last_err


def _atomic_write_json(path: Path, data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    last_err: Exception | None = None
    for _ in range(5):
        tmp_name = f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        tmp = path.with_name(tmp_name)
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last_err = e
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            time.sleep(0.2)
        except Exception as e:
            last_err = e
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            break
    if last_err:
        raise last_err


def load_music_queue() -> list[dict[str, Any]]:
    if not MUSIC_QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(MUSIC_QUEUE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            out: list[dict[str, Any]] = []
            for x in data:
                if isinstance(x, dict):
                    out.append(x)
            return out
        return []
    except Exception:
        return []


def save_music_queue(queue: list[dict[str, Any]]) -> None:
    _atomic_write_json(MUSIC_QUEUE_PATH, queue)


def enqueue_music_job(job: dict[str, Any]) -> None:
    with _music_submit_lock:
        q = load_music_queue()
        q.append(job)
        save_music_queue(q)


def clear_music_queue() -> int:
    with _music_submit_lock:
        q = load_music_queue()
        save_music_queue([])
        return len(q)


def _plan_hash(plan: dict[str, Any]) -> str:
    payload = {
        "lyrics": str(plan.get("lyrics", "") or ""),
        "prompt": str(plan.get("prompt", "") or ""),
        "model": str(plan.get("model", "") or ""),
        "n": int(plan.get("n", 1) or 1),
        "stream": bool(plan.get("stream", True)),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def ensure_music_worker_started() -> None:
    global _music_worker_started
    if _music_worker_started:
        return
    _music_worker_started = True

    global _music_lock_handle
    try:
        lease_ttl_s = int(os.getenv("MUSIC_WORKER_LEASE_TTL_S", "60"))
        now = time.time()
        if MUSIC_LOCK_PATH.exists():
            try:
                age = now - MUSIC_LOCK_PATH.stat().st_mtime
                if age < float(lease_ttl_s):
                    _music_lock_handle = None
                    return
                MUSIC_LOCK_PATH.unlink()
            except Exception:
                _music_lock_handle = None
                return

        fd = os.open(str(MUSIC_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        f = os.fdopen(fd, "w", encoding="utf-8")
        try:
            f.write(f"pid={os.getpid()} started_at={int(now)}\n")
            f.flush()
        except Exception:
            pass
        _music_lock_handle = f
    except Exception:
        _music_lock_handle = None
        return
    global _music_worker_pid, _music_worker_started_at
    try:
        _music_worker_pid = os.getpid()
    except Exception:
        _music_worker_pid = None
    _music_worker_started_at = int(time.time())

    def loop() -> None:
        while True:
            try:
                try:
                    os.utime(MUSIC_LOCK_PATH, None)
                except Exception:
                    pass
                with _music_submit_lock:
                    job = load_music_job()
                    current_task_id = str(job.get("task_id") or "").strip()
                    current_status = str(job.get("task_status") or "").strip().lower()
                    if current_task_id:
                        try:
                            task = mureka_query_task(current_task_id)
                            if isinstance(task, dict):
                                current_status = str(task.get("status") or "").strip().lower() or current_status
                                job["task_status"] = str(task.get("status") or job.get("task_status") or "")
                                job["trace_id"] = task.get("trace_id") or job.get("trace_id")
                                file_url = _pick_file_url_from_task(task)
                                stream_url = _pick_stream_url_from_task(task)
                                if file_url:
                                    job["audio_url"] = file_url
                                if stream_url:
                                    job["stream_url"] = stream_url
                                save_music_job(job)
                        except Exception:
                            current_status = ""

                    if current_task_id and current_status != "succeeded":
                        continue

                    q = load_music_queue()
                    if q:
                        item = q[0]
                        plan = item.get("plan")
                        if isinstance(plan, dict) and plan.get("lyrics") and plan.get("prompt"):
                            try:
                                item["submit_attempted_at"] = int(time.time())
                                item.pop("submit_error", None)
                                item.pop("submit_error_at", None)
                                save_music_queue(q)
                                job3 = load_music_job()
                                prev_task_id2 = str(job3.get("task_id") or "").strip()
                                prev_status2 = str(job3.get("task_status") or "").strip().lower()
                                if prev_task_id2 and prev_status2 != "succeeded":
                                    continue
                                task = mureka_generate_song(
                                    lyrics=str(plan.get("lyrics")),
                                    prompt=str(plan.get("prompt")),
                                    model=str(plan.get("model") or "auto"),
                                    n=int(plan.get("n", 1) or 1),
                                    stream=bool(plan.get("stream", True)),
                                )
                                task_id = str(task.get("id") or "")
                                if task_id:
                                    current = {
                                        "job_id": item.get("job_id"),
                                        "updated_at": int(time.time()),
                                        "description": item.get("description", ""),
                                        "plan": plan,
                                        "thought": item.get("thought", ""),
                                        "task": task,
                                        "task_id": task_id,
                                        "task_status": str(task.get("status") or ""),
                                        "trace_id": task.get("trace_id"),
                                        "model": task.get("model"),
                                        "last_submitted_hash": _plan_hash(plan),
                                        "submitted_at": int(time.time()),
                                    }
                                    save_music_job(current)
                                    q = q[1:]
                                    save_music_queue(q)
                            except Exception as e:
                                item["submit_error"] = str(e)
                                item["submit_error_at"] = int(time.time())
                                q[0] = item
                                save_music_queue(q)
            except Exception:
                pass
            time.sleep(10.0)

    threading.Thread(target=loop, daemon=True).start()


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
    music_job_id: str | None = None
    music: dict[str, Any] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_music_worker_started()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/simulator", response_class=HTMLResponse)
async def simulator(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("simulator.html", {"request": request})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class SimMidiAnalyzeRequest(BaseModel):
    midi_base64: str = Field(min_length=1, max_length=50_000_000)
    filename: str | None = None


class SimMidiRenderRequest(BaseModel):
    midi_base64: str = Field(min_length=1, max_length=50_000_000)
    instrument: str = Field(min_length=1, max_length=200)
    sr: int = Field(default=44100, ge=8000, le=96000)
    normalize: int = Field(default=28000, ge=1000, le=32767)


class SimScoreToMidiRequest(BaseModel):
    score_text: str = Field(min_length=1, max_length=2_000_000)
    do: str = Field(default="C4", min_length=1, max_length=10)
    key: str = Field(default="C major", min_length=1, max_length=30)
    ts: str = Field(default="4/4", min_length=1, max_length=10)
    bpm: int = Field(default=120, ge=20, le=300)
    tpq: int = Field(default=480, ge=60, le=1920)
    no_underscore_beats: float = Field(default=2.0, ge=0.125, le=8.0)
    vel: int = Field(default=96, ge=1, le=127)


class SimOcrRecognizeRequest(BaseModel):
    image_base64: str = Field(min_length=1, max_length=80_000_000)
    filename: str | None = None


def _ocr_annotation_path(sid: str) -> Path:
    return _ocr_web_dir / f"{sid}.json"


def _ocr_processed_path(sid: str) -> Path:
    return _ocr_web_dir / f"{sid}_processed.png"


def _ocr_load_annotation(sid: str) -> dict[str, Any] | None:
    p = _ocr_annotation_path(sid)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ocr_save_annotation(sid: str, data: dict[str, Any]) -> None:
    p = _ocr_annotation_path(sid)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _ocr_find_upload_path(sid: str) -> Path | None:
    try:
        for fp in _ocr_upload_dir.iterdir():
            if fp.is_file() and fp.name.startswith(sid):
                return fp
    except Exception:
        return None
    return None


def _ocr_rebuild_sequence_from_annotation(data: dict[str, Any]) -> str:
    if ocr_build_sequence_line is None:
        return ""
    lines = data.get("lines") or []
    items = data.get("items") or []
    seq_lines: list[str] = []
    for ln in lines:
        y0 = int(ln.get("y0", 0))
        y1 = int(ln.get("y1", 0))
        if y1 <= y0:
            continue
        in_line = []
        for it in items:
            x = float(it.get("x", 0))
            y = float(it.get("y", 0))
            w = float(it.get("w", 0))
            h = float(it.get("h", 0))
            cy = y + (h / 2.0)
            if cy >= y0 and cy <= y1:
                in_line.append(it)

        digit_boxes = []
        hline_boxes = []
        vline_boxes = []
        dot_boxes = []
        accidental_boxes = []
        for it in in_line:
            t = str(it.get("type", "")).strip()
            x = int(float(it.get("x", 0)))
            y = int(float(it.get("y", 0)))
            w = int(float(it.get("w", 0)))
            h = int(float(it.get("h", 0)))
            if w <= 0 or h <= 0:
                continue
            if t == "digit":
                txt = str(it.get("text", "")).strip()
                if not txt:
                    continue
                conf = int(it.get("conf", 100))
                digit_boxes.append((x, y - y0, w, h, txt, conf))
            elif t == "hline":
                hline_boxes.append((x, y - y0, w, h))
            elif t == "vline":
                vline_boxes.append((x, y - y0, w, h))
            elif t == "dot":
                dot_boxes.append((x, y - y0, w, h))
            elif t == "accidental":
                txt = str(it.get("text", "")).strip()
                if txt not in ("#", "b"):
                    continue
                conf = int(it.get("conf", 100))
                accidental_boxes.append((x, y - y0, w, h, txt, conf))

        digit_boxes.sort(key=lambda d: d[0])
        hline_boxes.sort(key=lambda b: b[0])
        vline_boxes.sort(key=lambda b: b[0])
        dot_boxes.sort(key=lambda b: b[0])
        accidental_boxes.sort(key=lambda b: b[0])
        seq_lines.append(ocr_build_sequence_line(digit_boxes, hline_boxes, dot_boxes, vline_boxes, accidental_boxes))
    return "\n".join(seq_lines)


def _sim_resolve_sample_leaf(dir_path: Path) -> Path:
    nested = dir_path / "钢琴88键独立音频文件"
    if nested.is_dir():
        return nested
    return dir_path


def _sim_is_valid_88key_sample_dir(dir_path: Path) -> bool:
    if sim_engine is None:
        return False
    leaf = _sim_resolve_sample_leaf(dir_path)
    if not leaf.is_dir():
        return False
    for midi in range(21, 109):
        name = sim_engine.midi_to_note_name(midi)
        if not (leaf / f"{name}.wav").is_file():
            return False
    return True


def _sim_list_instruments() -> list[dict[str, str]]:
    root = SIM_SAMPLES_ROOT
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    try:
        entries = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)
    except Exception:
        entries = []
    for p in entries:
        leaf = _sim_resolve_sample_leaf(p)
        missing = 0
        if sim_engine is None:
            missing = 88
        else:
            for midi in range(21, 109):
                name = sim_engine.midi_to_note_name(midi)
                if not (leaf / f"{name}.wav").is_file():
                    missing += 1
        out.append({"name": p.name, "path": str(p), "valid": missing == 0, "missing": missing})
    if out:
        return out
    if _sim_is_valid_88key_sample_dir(root):
        out.append({"name": root.name, "path": str(root), "valid": True, "missing": 0})
    return out


def _sim_pick_instrument_dir(name: str) -> Path:
    name = name.strip()
    instruments = _sim_list_instruments()
    for it in instruments:
        if it["name"] == name:
            if not it.get("valid", False):
                raise HTTPException(status_code=400, detail="该音色缺少采样文件，请补齐 88 个音。")
            return Path(it["path"])
    raise HTTPException(status_code=400, detail="无效音色：请从下拉框选择。")


def _sim_decode_midi_to_file(b64: str, suffix: str) -> Path:
    try:
        raw = base64.b64decode(b64.encode("utf-8"), validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="MIDI 数据不是有效的 base64。")
    tmp = SIM_OUTPUT_DIR / f"midi_{uuid.uuid4().hex}{suffix}"
    tmp.write_bytes(raw)
    return tmp


@app.get("/api/sim/instruments")
async def sim_instruments() -> dict[str, Any]:
    if sim_engine is None:
        raise HTTPException(status_code=500, detail="模拟演奏模块未加载。")
    return {"items": _sim_list_instruments(), "root": str(SIM_SAMPLES_ROOT)}


@app.post("/api/sim/midi/analyze")
async def sim_midi_analyze(req: SimMidiAnalyzeRequest) -> dict[str, Any]:
    if sim_engine is None:
        raise HTTPException(status_code=500, detail="模拟演奏模块未加载。")
    suffix = ".mid"
    if req.filename:
        ext = os.path.splitext(req.filename)[1].lower()
        if ext in (".mid", ".midi"):
            suffix = ext
    midi_path = _sim_decode_midi_to_file(req.midi_base64, suffix)
    try:
        tpq, tempos, spans = sim_engine.parse_midi(str(midi_path))
        tempo_points = sim_engine.build_tempo_map(tpq, tempos)
        events = []
        out_range = 0
        in_range = 0
        max_end = 0.0
        for sp in spans:
            if sp.note < 21 or sp.note > 108:
                out_range += 1
                continue
            if sp.end_tick <= sp.start_tick:
                continue
            st = sim_engine.tick_to_seconds(sp.start_tick, tpq, tempo_points)
            et = sim_engine.tick_to_seconds(sp.end_tick, tpq, tempo_points)
            if et <= st:
                continue
            in_range += 1
            if et > max_end:
                max_end = et
            events.append(
                {
                    "start_s": st,
                    "end_s": et,
                    "note": sp.note,
                    "note_name": sim_engine.midi_to_note_name(sp.note),
                    "velocity": sp.velocity,
                    "channel": sp.channel,
                }
            )
        events.sort(key=lambda e: (e["start_s"], e["note"], e["channel"]))
        return {
            "total_seconds": max_end,
            "in_range": in_range,
            "out_range": out_range,
            "events": events,
        }
    finally:
        try:
            midi_path.unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/api/sim/score/to_midi")
async def sim_score_to_midi(req: SimScoreToMidiRequest) -> dict[str, Any]:
    if sim_engine is None:
        raise HTTPException(status_code=500, detail="模拟演奏模块未加载。")
    if score_engine is None:
        raise HTTPException(status_code=500, detail="乐谱转MIDI模块未加载。")

    try:
        do_midi = score_engine.note_name_to_midi(req.do)
        time_sig = score_engine.parse_time_signature(req.ts)
        key_sig = score_engine.parse_key_signature(req.key)
        tokens = score_engine.tokenize(req.score_text)
        spans, total_ticks = score_engine.build_spans(
            tokens,
            do_midi=int(do_midi),
            bpm=int(req.bpm),
            time_sig=time_sig,
            tpq=int(req.tpq),
            no_underscore_beats=float(req.no_underscore_beats),
            velocity=int(req.vel),
            mode="major",
        )
        midi_bytes = score_engine.build_midi_bytes(
            spans=spans,
            total_ticks=int(total_ticks),
            tpq=int(req.tpq),
            bpm=int(req.bpm),
            time_sig=time_sig,
            key_sig=key_sig,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="转 MIDI 失败。")

    midi_b64 = base64.b64encode(midi_bytes).decode("utf-8")
    tmp = SIM_OUTPUT_DIR / f"score_{uuid.uuid4().hex}.mid"
    tmp.write_bytes(midi_bytes)
    try:
        tpq2, tempos, spans2 = sim_engine.parse_midi(str(tmp))
        tempo_points = sim_engine.build_tempo_map(tpq2, tempos)
        events = []
        out_range = 0
        in_range = 0
        max_end = 0.0
        for sp in spans2:
            if sp.note < 21 or sp.note > 108:
                out_range += 1
                continue
            if sp.end_tick <= sp.start_tick:
                continue
            st = sim_engine.tick_to_seconds(sp.start_tick, tpq2, tempo_points)
            et = sim_engine.tick_to_seconds(sp.end_tick, tpq2, tempo_points)
            if et <= st:
                continue
            in_range += 1
            if et > max_end:
                max_end = et
            events.append(
                {
                    "start_s": st,
                    "end_s": et,
                    "note": sp.note,
                    "note_name": sim_engine.midi_to_note_name(sp.note),
                    "velocity": sp.velocity,
                    "channel": sp.channel,
                }
            )
        events.sort(key=lambda e: (e["start_s"], e["note"], e["channel"]))
        return {
            "midi_base64": midi_b64,
            "total_seconds": max_end,
            "in_range": in_range,
            "out_range": out_range,
            "events": events,
        }
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/api/sim/ocr/recognize")
async def sim_ocr_recognize(req: SimOcrRecognizeRequest) -> dict[str, Any]:
    if ocr_pre_processing is None:
        raise HTTPException(status_code=500, detail="OCR 模块未加载（缺少依赖或初始化失败）。")

    b64 = req.image_base64.strip()
    if "," in b64:
        b64 = b64.split(",", 1)[1].strip()
    try:
        raw = base64.b64decode(b64.encode("utf-8"), validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="图片数据不是有效的 base64。")

    filename = (req.filename or "").strip()
    ext = os.path.splitext(filename)[1].lower() if filename else ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        ext = ".png"

    try:
        import cv2
        import numpy as np
    except Exception:
        raise HTTPException(status_code=500, detail="OCR 依赖未安装（需要 opencv-python / numpy）。")

    sid = uuid.uuid4().hex
    in_path = _ocr_upload_dir / f"{sid}{ext}"
    in_path.write_bytes(raw)

    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="图片读取失败。")

    try:
        out_img, seq_text, det = ocr_pre_processing(img, str(in_path), return_detections=True)
    except Exception:
        raise HTTPException(status_code=500, detail="OCR 识别失败。")

    out_path = _ocr_processed_path(sid)
    try:
        ok, enc = cv2.imencode(".png", out_img)
        if ok:
            out_path.write_bytes(enc.tobytes())
    except Exception:
        pass

    lines = (det or {}).get("lines", []) if isinstance(det, dict) else []
    items = []
    for ln in lines:
        for it in ln.get("items", []) if isinstance(ln, dict) else []:
            it = dict(it)
            it["line_y0"] = ln.get("y0", 0)
            it["line_y1"] = ln.get("y1", 0)
            items.append(it)
    anno = {"sid": sid, "lines": [{"y0": ln.get("y0", 0), "y1": ln.get("y1", 0)} for ln in lines], "items": items}
    _ocr_save_annotation(sid, anno)
    (_ocr_score_dir / f"{sid}.txt").write_text(str(seq_text or ""), encoding="utf-8")

    return {
        "sid": sid,
        "sequence": str(seq_text or ""),
        "processed_url": f"/api/sim/ocr/processed/{sid}",
        "original_url": f"/api/sim/ocr/original/{sid}",
    }


@app.get("/api/sim/ocr/annotation/{sid}")
async def sim_ocr_get_annotation(sid: str) -> dict[str, Any]:
    data = _ocr_load_annotation(sid)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    data = dict(data)
    data["image_url"] = f"/api/sim/ocr/original/{sid}"
    data["processed_url"] = f"/api/sim/ocr/processed/{sid}"
    txt_path = _ocr_score_dir / f"{sid}.txt"
    if txt_path.is_file():
        data["sequence"] = txt_path.read_text(encoding="utf-8", errors="ignore")
    else:
        data["sequence"] = _ocr_rebuild_sequence_from_annotation(data)
    return data


@app.post("/api/sim/ocr/annotation/{sid}")
async def sim_ocr_save_annotation(sid: str, request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad json")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="bad json")
    if str(data.get("sid", sid)) != sid:
        data["sid"] = sid

    items = data.get("items") or []
    if isinstance(items, list):
        accs = [it for it in items if isinstance(it, dict) and str(it.get("type")) == "accidental"]
        if accs:
            filtered = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                if str(it.get("type")) == "accidental":
                    filtered.append(it)
                    continue
                x = float(it.get("x", 0))
                y = float(it.get("y", 0))
                w = float(it.get("w", 0))
                h = float(it.get("h", 0))
                cx = x + (w / 2.0)
                cy = y + (h / 2.0)
                inside = False
                for a in accs:
                    ax = float(a.get("x", 0))
                    ay = float(a.get("y", 0))
                    aw = float(a.get("w", 0))
                    ah = float(a.get("h", 0))
                    if cx >= ax and cx <= (ax + aw) and cy >= ay and cy <= (ay + ah):
                        inside = True
                        break
                if not inside:
                    filtered.append(it)
            data["items"] = filtered

    _ocr_save_annotation(sid, data)
    seq_text = _ocr_rebuild_sequence_from_annotation(data)
    (_ocr_score_dir / f"{sid}.txt").write_text(seq_text, encoding="utf-8")
    return {"ok": True, "sequence": seq_text}


@app.get("/api/sim/ocr/processed/{sid}")
async def sim_ocr_processed(sid: str):
    p = _ocr_processed_path(sid)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p), media_type="image/png", filename=p.name)


@app.get("/api/sim/ocr/original/{sid}")
async def sim_ocr_original(sid: str):
    p = _ocr_find_upload_path(sid)
    if p is None or not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    ext = p.suffix.lower()
    mt = "application/octet-stream"
    if ext == ".png":
        mt = "image/png"
    elif ext in (".jpg", ".jpeg"):
        mt = "image/jpeg"
    elif ext == ".bmp":
        mt = "image/bmp"
    elif ext == ".webp":
        mt = "image/webp"
    return FileResponse(str(p), media_type=mt, filename=p.name)


@app.post("/api/sim/midi/render")
async def sim_midi_render(req: SimMidiRenderRequest) -> dict[str, Any]:
    if sim_engine is None:
        raise HTTPException(status_code=500, detail="模拟演奏模块未加载。")
    inst_dir = _sim_pick_instrument_dir(req.instrument)
    midi_path = _sim_decode_midi_to_file(req.midi_base64, ".mid")
    wav_id = uuid.uuid4().hex
    out_wav = SIM_OUTPUT_DIR / f"{wav_id}.wav"
    try:
        tpq, tempos, spans = sim_engine.parse_midi(str(midi_path))
        sim_engine.render(
            spans=spans,
            tpq=tpq,
            tempos=tempos,
            sample_root=str(inst_dir),
            out_wav=str(out_wav),
            target_sr=int(req.sr),
            normalize_peak=int(req.normalize),
        )
        _sim_wav_store[wav_id] = str(out_wav)
        return {"wav_id": wav_id, "wav_url": f"/api/sim/wav/{wav_id}"}
    finally:
        try:
            midi_path.unlink(missing_ok=True)
        except Exception:
            pass


@app.get("/api/sim/wav/{wav_id}")
async def sim_wav(wav_id: str):
    path = _sim_wav_store.get(wav_id, "")
    if not path:
        raise HTTPException(status_code=404, detail="WAV 不存在或已过期。")
    fp = Path(path)
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="WAV 文件不存在。")
    return FileResponse(str(fp), media_type="audio/wav", filename=fp.name)


@app.get("/api/music/worker")
async def music_worker() -> dict[str, Any]:
    job = load_music_job()
    q = load_music_queue()
    return {
        "pid": _music_worker_pid,
        "started_at": _music_worker_started_at,
        "lock_path": str(MUSIC_LOCK_PATH),
        "job_path": str(MUSIC_JOB_PATH),
        "queue_path": str(MUSIC_QUEUE_PATH),
        "lock_opened": _music_lock_handle is not None,
        "active": is_mureka_task_active(job),
        "hint": get_mureka_task_hint(job),
        "queue_len": len(q),
    }


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


def _extract_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    import json

    candidate = s[start : end + 1]
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_between(text: str, start_tag: str, end_tag: str) -> str:
    s = (text or "")
    start = s.find(start_tag)
    if start == -1:
        return ""
    start += len(start_tag)
    end = s.find(end_tag, start)
    if end == -1:
        return ""
    return s[start:end].strip("\n\r\t ").strip()


def extract_music_plan(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    data = _extract_json_object(raw)
    if isinstance(data, dict) and (data.get("lyrics") or data.get("prompt")):
        return data

    lyrics = _extract_between(raw, "<MUREKA_LYRICS>", "</MUREKA_LYRICS>")
    prompt = _extract_between(raw, "<MUREKA_PROMPT>", "</MUREKA_PROMPT>")
    meta_text = _extract_between(raw, "<MUREKA_META>", "</MUREKA_META>")
    meta = _extract_json_object(meta_text) if meta_text else None

    if not lyrics:
        lyrics = _extract_between(raw, "[LYRICS]", "[/LYRICS]")
    if not prompt:
        prompt = _extract_between(raw, "[PROMPT]", "[/PROMPT]")
    if meta is None:
        meta = _extract_json_object(_extract_between(raw, "[META]", "[/META]")) if "[META]" in raw else None

    out: dict[str, Any] = {}
    thought = _extract_between(raw, "<MUREKA_THOUGHT>", "</MUREKA_THOUGHT>")
    if thought:
        out["thought"] = thought
    if lyrics:
        out["lyrics"] = lyrics
    if prompt:
        out["prompt"] = prompt
    if isinstance(meta, dict):
        out.update(meta)
    return out


def deepseek_music_plan(user_desc: str) -> dict[str, Any]:
    sys = get_system_prompt()
    user = f"【音乐生成模式】{user_desc}"
    text, _ = deepseek_chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=700,
    )
    data = extract_music_plan(text)

    thought = str(data.get("thought", "") or "").strip()
    lyrics = str(data.get("lyrics", "") or "").strip()
    prompt = str(data.get("prompt", "") or "").strip()
    model = str(data.get("model", "") or "").strip() or "auto"
    n_raw = data.get("n", 1)
    stream_raw = data.get("stream", True)

    try:
        n = int(n_raw)
    except Exception:
        n = 1
    if n < 1:
        n = 1
    if n > 3:
        n = 3

    stream = bool(stream_raw)

    if not lyrics:
        lyrics = f"[Verse]\n{user_desc}\n\n[Chorus]\n民族的旋律，讲述山河与乡音\n"
    if len(lyrics) > 3000:
        lyrics = lyrics[:3000].rstrip()
    if not prompt:
        prompt = "Chinese ethnic, educational, warm, acoustic, moderate tempo, traditional instruments"
    if len(prompt) > 1024:
        prompt = prompt[:1024].rstrip()

    return {"thought": thought, "lyrics": lyrics, "prompt": prompt, "model": model, "n": n, "stream": stream}


def is_music_request(text: str) -> bool:
    t = (text or "").lower()
    keys = [
        "生成音乐",
        "生成一首",
        "写歌",
        "作曲",
        "谱曲",
        "编曲",
        "配乐",
        "bgm",
        "music",
        "song",
    ]
    return any(k in t for k in keys)

def is_music_cancel_request(text: str) -> bool:
    t = (text or "").lower()
    if any(k in t for k in ("取消", "终止", "停止", "清空")) and any(
        k in t for k in ("音乐", "mureka", "作曲", "生成", "队列")
    ):
        return True
    return False


def mureka_generate_song(*, lyrics: str, prompt: str, model: str = "auto", n: int = 1, stream: bool = True) -> dict[str, Any]:
    api_key = get_mureka_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail="未检测到 Mureka API key，请在 mureka_api_key.txt 中填写。")
    url = f"{get_mureka_base_url()}/v1/song/generate"
    payload: dict[str, Any] = {"lyrics": lyrics, "model": model, "prompt": prompt, "n": n, "stream": stream}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=90)
    except requests.Timeout:
        raise HTTPException(status_code=502, detail="Mureka 无响应（请求超时）。")
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Mureka 无响应（请求失败）。")
    if resp.status_code >= 400:
        try:
            err = resp.json()
            msg = err.get("error", {}).get("message") or err.get("message") or resp.text
            trace_id = err.get("trace_id")
        except Exception:
            msg = resp.text
            trace_id = None
        base = get_mureka_base_url()
        extra = f"（base: {base}" + (f"，trace_id: {trace_id}" if trace_id else "") + ")"
        raise HTTPException(status_code=502, detail=f"Mureka 返回错误：{msg}{extra}")
    return resp.json()


def mureka_query_task(task_id: str) -> dict[str, Any]:
    api_key = get_mureka_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail="未检测到 Mureka API key，请在 mureka_api_key.txt 中填写。")
    url = f"{get_mureka_base_url()}/v1/song/query/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=60)
    except requests.Timeout:
        raise HTTPException(status_code=502, detail="Mureka 无响应（查询超时）。")
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Mureka 无响应（查询失败）。")
    if resp.status_code >= 400:
        try:
            err = resp.json()
            msg = err.get("error", {}).get("message") or err.get("message") or resp.text
            trace_id = err.get("trace_id")
        except Exception:
            msg = resp.text
            trace_id = None
        base = get_mureka_base_url()
        extra = f"（base: {base}" + (f"，trace_id: {trace_id}" if trace_id else "") + ")"
        raise HTTPException(status_code=502, detail=f"Mureka 查询返回错误：{msg}{extra}")
    return resp.json()


def submit_music_job_once(job: dict[str, Any]) -> None:
    plan = job.get("plan")
    if not isinstance(plan, dict):
        return
    if not plan.get("lyrics") or not plan.get("prompt"):
        return
    if job.get("task_id"):
        return

    with _music_submit_lock:
        try:
            current = load_music_job()
            if is_mureka_task_active(current):
                return
            if current.get("task_id"):
                return
            plan2 = current.get("plan")
            if not isinstance(plan2, dict):
                return
            h = _plan_hash(plan2)
            current["last_attempt_at"] = int(time.time())
            save_music_job(current)

            task = mureka_generate_song(
                lyrics=str(plan2.get("lyrics")),
                prompt=str(plan2.get("prompt")),
                model=str(plan2.get("model") or "auto"),
                n=int(plan2.get("n", 1) or 1),
                stream=bool(plan2.get("stream", True)),
            )
            current["task"] = task
            current["task_id"] = str(task.get("id") or "")
            current["task_status"] = str(task.get("status") or "")
            current["trace_id"] = task.get("trace_id")
            current["model"] = task.get("model")
            current["last_submitted_hash"] = h
            current["submitted_at"] = int(time.time())
            current.pop("error", None)
            current.pop("retry_count", None)
            current.pop("next_retry_at", None)
            save_music_job(current)
        except Exception as e:
            current = load_music_job()
            if current.get("task_id"):
                return
            current["error"] = str(e)
            current["last_attempt_at"] = int(time.time())
            plan3 = current.get("plan")
            if isinstance(plan3, dict):
                try:
                    current["last_submitted_hash"] = _plan_hash(plan3)
                except Exception:
                    pass
            current.pop("retry_count", None)
            current.pop("next_retry_at", None)
            save_music_job(current)


def _extract_audio_candidates(choice: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in ("audio_url", "song_url", "url", "stream_url", "download_url"):
        v = choice.get(k)
        if isinstance(v, str) and v.startswith("http"):
            out[k] = v
    return out


def _sanitize_url(u: str) -> str:
    s = (u or "").strip()
    if not s:
        return ""
    if s[0] in ("`", '"', "'"):
        s = s.strip("`\"'")
    return s.strip()


def _pick_file_url_from_task(task: dict[str, Any]) -> str:
    choices = task.get("choices")
    if not isinstance(choices, list):
        return ""
    for c in choices:
        if not isinstance(c, dict):
            continue
        urls = _extract_audio_candidates(c)
        for k in ("url", "audio_url", "song_url", "download_url"):
            v = urls.get(k)
            if v:
                return _sanitize_url(v)
    return ""


def _pick_stream_url_from_task(task: dict[str, Any]) -> str:
    choices = task.get("choices")
    if not isinstance(choices, list):
        return ""
    for c in choices:
        if not isinstance(c, dict):
            continue
        urls = _extract_audio_candidates(c)
        v = urls.get("stream_url")
        if v:
            return _sanitize_url(v)
    return ""


def _is_probably_mp3(*, url: str, content_type: str, first_chunk: bytes) -> bool:
    u = (url or "").lower().split("?", 1)[0]
    ct = (content_type or "").lower()
    if u.endswith(".mp3"):
        return True
    if "audio/mpeg" in ct or "audio/mp3" in ct:
        return True
    if first_chunk.startswith(b"ID3"):
        return True
    if len(first_chunk) >= 2 and first_chunk[0] == 0xFF and first_chunk[1] in (0xFB, 0xF3, 0xF2):
        return True
    return False


def _cleanup_tmp_dir(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


@app.get("/api/music/mp3")
async def music_mp3(task_id: str) -> Any:
    wait_s = int(os.getenv("MUREKA_MP3_WAIT_S", "120"))
    start = time.time()
    last_task: dict[str, Any] = {}
    audio_url = ""
    stream_url = ""
    while time.time() - start <= float(wait_s):
        task = mureka_query_task(task_id)
        last_task = task if isinstance(task, dict) else {}
        audio_url = _pick_file_url_from_task(last_task)
        stream_url = _pick_stream_url_from_task(last_task)
        if audio_url:
            break
        status = str((last_task or {}).get("status") or "").lower()
        if status in _MUREKA_TERMINAL_STATUSES:
            break
        time.sleep(2.0)

    if not audio_url:
        if stream_url:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise HTTPException(status_code=409, detail="任务尚未产出 MP3，请稍后重试（当前只有流式 AAC）。")
            audio_url = stream_url
        else:
            raise HTTPException(status_code=404, detail="未找到可下载的音频链接。")

    try:
        resp = requests.get(audio_url, stream=True, timeout=60)
    except requests.Timeout:
        raise HTTPException(status_code=502, detail="音频下载失败：请求超时。")
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="音频下载失败：请求异常。")

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"音频下载失败：HTTP {resp.status_code}。")

    it = resp.iter_content(chunk_size=256 * 1024)
    first = next(it, b"")
    if not first:
        resp.close()
        raise HTTPException(status_code=502, detail="音频下载失败：空响应。")

    is_mp3 = _is_probably_mp3(url=audio_url, content_type=resp.headers.get("content-type", ""), first_chunk=first)
    filename = f"mureka_{task_id}.mp3"

    if is_mp3:
        def gen():
            try:
                yield first
                for chunk in it:
                    if chunk:
                        yield chunk
            finally:
                resp.close()

        return StreamingResponse(
            gen(),
            media_type="audio/mpeg",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        resp.close()
        raise HTTPException(status_code=501, detail="音频不是 MP3，服务器未安装 ffmpeg，无法转码。")

    tmpdir = tempfile.mkdtemp(prefix="mureka_mp3_")
    in_path = os.path.join(tmpdir, "in.bin")
    out_path = os.path.join(tmpdir, filename)
    try:
        with open(in_path, "wb") as f:
            f.write(first)
            for chunk in it:
                if chunk:
                    f.write(chunk)
    finally:
        resp.close()

    cmd = [ffmpeg, "-y", "-i", in_path, "-vn", "-codec:a", "libmp3lame", "-q:a", "2", out_path]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(out_path):
        err = (p.stderr or p.stdout or "").strip()
        if len(err) > 400:
            err = err[-400:]
        raise HTTPException(status_code=502, detail=f"转码失败：{err or 'unknown'}")

    return FileResponse(
        out_path,
        media_type="audio/mpeg",
        filename=filename,
        background=BackgroundTask(_cleanup_tmp_dir, tmpdir),
    )


def mureka_wait_result(task_id: str, *, timeout_s: int = 45, interval_s: float = 2.0) -> dict[str, Any]:
    start = time.time()
    last_task: dict[str, Any] | None = None
    while time.time() - start <= float(timeout_s):
        try:
            task = mureka_query_task(task_id)
        except Exception as e:
            return {"status": "failed", "task": last_task or {}, "audio_url": "", "error": str(e)}
        last_task = task if isinstance(task, dict) else None
        status = str((task or {}).get("status") or "").lower()
        file_url = _pick_file_url_from_task(task or {})
        stream_url = _pick_stream_url_from_task(task or {})
        if file_url:
            return {"status": status or "streaming", "task": task, "audio_url": file_url, "stream_url": stream_url}
        if stream_url:
            return {"status": status or "streaming", "task": task, "audio_url": "", "stream_url": stream_url}
        if status in _MUREKA_TERMINAL_STATUSES:
            return {"status": status, "task": task, "audio_url": "", "stream_url": stream_url}
        time.sleep(float(interval_s))
    return {
        "status": "no_response",
        "task": last_task or {},
        "audio_url": "",
        "stream_url": _pick_stream_url_from_task(last_task or {}),
    }


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

    kg_triples = _kg.query(user_query, top_k=10, expand=10)
    kg_text = format_triples(kg_triples)
    if kg_text:
        if len(kg_text) > 1500:
            kg_text = kg_text[:1500].rstrip()
        sys_content = sys_content + "\n\n知识图谱（CSV，自动匹配）：\n" + kg_text
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

    music_job_id: str | None = None
    music: dict[str, Any] | None = None
    if is_music_cancel_request(user_msg):
        cleared = clear_music_job(reason=user_msg)
        prev = cleared.get("previous") if isinstance(cleared, dict) else None
        hint = ""
        if isinstance(prev, dict):
            tid = str(prev.get("task_id") or "").strip()
            st = str(prev.get("task_status") or "").strip()
            if tid:
                hint = f"（已清空本地队列：task_id={tid}" + (f"，status={st}" if st else "") + "）"
        answer = "已终止本地 Mureka 生成流程并清空等待队列。" + hint + "\n注意：当前版本无法通过 API 强制取消 Mureka 服务端已创建的任务。"
        usage = None
        ref_mode = (req.ref_mode or "rag").strip()
        messages.append({"role": "assistant", "content": answer})
        save_session(
            SESSIONS_DIR,
            sid,
            summary,
            messages[-24:],
            last_prompt=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}],
            last_ref_mode=ref_mode,
            last_usage={},
            last_summary_usage=summary_usage or {},
        )
        return ChatV2Response(
            session_id=sid,
            answer=answer,
            usage=None,
            summary_usage=summary_usage,
            ref_mode=ref_mode,
            music_job_id=None,
            music=None,
        )

    if is_music_request(user_msg):
        plan = deepseek_music_plan(user_msg)
        thought = str(plan.get("thought", "") or "").strip()
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id,
            "created_at": int(time.time()),
            "description": user_msg,
            "plan": {
                "lyrics": plan.get("lyrics", ""),
                "prompt": plan.get("prompt", ""),
                "model": plan.get("model", "auto"),
                "n": plan.get("n", 1),
                "stream": plan.get("stream", True),
            },
            "thought": thought,
        }
        with _music_submit_lock:
            q = load_music_queue()
            q.append(job)
            save_music_queue(q)
            queue_pos = len(q)
        music_job_id = job_id
        answer = thought or f"已生成作曲计划，已加入队列（第 {queue_pos} 位）。系统将以 10 秒为间隔尝试提交至 Mureka。"
        usage = None
        ref_mode = (req.ref_mode or "rag").strip()
        music = {"status": "queued", "queue_pos": queue_pos}
    else:
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
        last_prompt=(llm_messages if not is_music_request(user_msg) else [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}]),
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
        music_job_id=music_job_id,
        music=music,
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


class MusicGenerateRequest(BaseModel):
    session_id: str | None = None
    description: str = Field(min_length=1, max_length=2000)


class MusicGenerateResponse(BaseModel):
    job_id: str
    plan: dict[str, Any]
    task_id: str | None = None
    task_status: str | None = None
    trace_id: str | None = None
    model: str | None = None


class MusicQueryResponse(BaseModel):
    task: dict[str, Any]
    audios: list[dict[str, Any]]


@app.post("/api/music/generate", response_model=MusicGenerateResponse)
async def music_generate(req: MusicGenerateRequest) -> MusicGenerateResponse:
    plan = deepseek_music_plan(req.description)
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "updated_at": int(time.time()),
        "description": req.description,
        "plan": plan,
    }
    save_music_job(job)
    return MusicGenerateResponse(job_id=job_id, plan=plan)


@app.get("/api/music/job")
async def music_job() -> dict[str, Any]:
    current = load_music_job()
    q = load_music_queue()
    queue_view: list[dict[str, Any]] = []
    for x in q[:20]:
        queue_view.append(
            {
                "job_id": x.get("job_id"),
                "created_at": x.get("created_at"),
                "description": x.get("description", ""),
                "submit_attempted_at": x.get("submit_attempted_at"),
                "submit_error": x.get("submit_error"),
                "submit_error_at": x.get("submit_error_at"),
            }
        )
    return {"current": current, "queue_len": len(q), "queue": queue_view}


class MusicClearRequest(BaseModel):
    reason: str = Field(default="manual_clear", max_length=2000)


@app.post("/api/music/clear")
async def music_clear(req: MusicClearRequest) -> dict[str, Any]:
    return clear_music_job(reason=req.reason)


@app.get("/api/music/query", response_model=MusicQueryResponse)
async def music_query(task_id: str) -> MusicQueryResponse:
    task = mureka_query_task(task_id)
    audios: list[dict[str, Any]] = []
    choices = task.get("choices")
    if isinstance(choices, list):
        for i, c in enumerate(choices):
            if not isinstance(c, dict):
                continue
            urls = _extract_audio_candidates(c)
            audios.append({"index": i, "urls": urls, "raw": c})
    return MusicQueryResponse(task=task, audios=audios)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port_text = os.getenv("PORT", "8009")
    try:
        port = int(port_text)
    except ValueError:
        port = 8009
    uvicorn.run(app, host=host, port=port, reload=False)

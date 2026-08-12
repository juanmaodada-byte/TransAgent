"""
TransAgent API 服务入口
=======================
v1.0 | 2026-08-06

FastAPI 服务，提供前端对接的 REST API + SSE 流式推送。

启动:
    cd transagent
    python -m backend.server

或:
    uvicorn transagent.backend.server:app --reload --port 8000
"""

import asyncio
import json
import os
import sys

# D6：修复 Windows 控制台 GBK 编码问题。
# agent_framework 等模块会打印 ✓/✗ 等 Unicode 字符，
# 在 GBK 控制台下 print 会抛 UnicodeEncodeError，导致翻译流程被误判失败。
try:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from transagent.interface import (
    TranslationSession, StepState, TermEntry,
)
from transagent.backend.config import get_config
from transagent.backend.core.orchestrator import Orchestrator
from transagent.backend.pipeline.preprocess import detect_format
from transagent.backend.pipeline.exporter import export_to_format

app = FastAPI(title="TransAgent API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内存中的会话存储（MVP阶段·后续迁移到Redis）
_sessions: dict[str, TranslationSession] = {}

# 术语确认断点注册表：session_id → {"event": asyncio.Event, "result": list}
# 翻译任务在术语确认处暂停并等待 /api/confirm_terms 唤醒。
_confirm_requests: dict[str, dict] = {}

# 术语确认等待超时（秒）·超时则自动接受并继续（前端断线等场景）
CONFIRM_TIMEOUT_SECONDS = 120


# ── 文件上传 ───────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件，返回file_id + 格式检测结果"""
    cfg = get_config().app

    # 保存文件
    os.makedirs(cfg.workspace_dir, exist_ok=True)
    import uuid
    file_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename or "unknown.txt")[1]
    file_path = os.path.join(cfg.workspace_dir, f"{file_id}{ext}")

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 格式检测
    try:
        fmt = detect_format(file_path)
    except Exception as e:
        return {"error": str(e), "file_id": file_id}

    return {
        "file_id": file_id,
        "format": fmt.format_type,
        "filename": file.filename,
        "size_kb": round(fmt.size_bytes / 1024, 1),
        "page_count": fmt.page_count,
        "md_preview": None,  # 前端请求时再加载
    }


# ── 翻译（SSE流式）──────────────────────────────────────────────────
# D6 修复：on_progress 从 async generator 改为「同步回调 + asyncio.Queue」。
# 原因：Orchestrator 以同步方式调用 on_progress（不 await、不迭代），
#       若 on_progress 是 async generator，yield 的 event 永远不会被消费，
#       导致前端收不到任何 progress 事件。
# 现方案：后台任务运行 translate()，progress 实时推入队列，
#         event_stream 从队列消费并实时 yield（真流式）。

@app.post("/api/translate")
async def translate(file_id: str = Form(...), user_id: str = Form("demo_user")):
    """启动翻译，SSE流式返回进度+结果"""

    cfg = get_config().app
    ext = _find_ext(file_id)
    file_path = os.path.join(cfg.workspace_dir, f"{file_id}{ext}")

    if not os.path.exists(file_path):
        return {"error": f"文件不存在: {file_id}"}

    async def event_stream():
        # 进度事件队列（生产者：后台 translate 任务；消费者：本生成器）
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        def on_progress(step: str, state: StepState, msg: str):
            """同步进度回调 → 推入队列（Orchestrator 同步调用）"""
            queue.put_nowait({
                "type": "progress",
                "step": step,
                "state": state.value if hasattr(state, "value") else state,
                "message": msg,
            })

        async def on_terms_pending(session, pending_terms):
            """术语确认断点回调（async）：
            注册确认请求 → 推送 terms_pending 事件 → 等待 /api/confirm_terms 唤醒。
            超时自动接受；会话被取消则抛 CancelledError。"""
            req = {
                "event": asyncio.Event(),
                "result": None,
            }
            _confirm_requests[session.session_id] = req

            try:
                # 推送待确认术语详情给前端
                print(f"[Confirm] 会话 {session.session_id} 进入断点，{len(pending_terms)} 个术语待确认")
                await queue.put({
                    "type": "__terms_pending__",
                    "session_id": session.session_id,
                    "terms": [t.to_dict() for t in pending_terms],
                })

                # 等待前端确认（带超时保护）
                try:
                    await asyncio.wait_for(req["event"].wait(),
                                           timeout=CONFIRM_TIMEOUT_SECONDS)
                    print(f"[Confirm] 会话 {session.session_id} 已唤醒·继续翻译")
                except asyncio.TimeoutError:
                    # 超时：自动接受低置信度术语
                    print(f"[Confirm] 会话 {session.session_id} 确认超时·自动接受")
                    return pending_terms

                result = req["result"]
                if result is None:
                    return pending_terms
                # 还原为 TermEntry 对象
                from transagent.interface import TermEntry
                return [
                    t if isinstance(t, TermEntry) else TermEntry.from_dict(t)
                    for t in result
                ]
            finally:
                _confirm_requests.pop(session.session_id, None)

        async def run_translate():
            """后台运行翻译，完成后推送 session"""
            try:
                orchestrator = Orchestrator(user_id=user_id)
                session = await orchestrator.translate(
                    file_path=file_path,
                    on_progress=on_progress,
                    on_terms_pending=on_terms_pending,  # 完整闭环：暂停等待用户确认
                )
                await queue.put({"type": "__session__", "session": session})
            except Exception as e:
                await queue.put({"type": "__error__", "message": str(e)})

        task = asyncio.create_task(run_translate())

        try:
            while True:
                ev = await queue.get()

                # ── 翻译完成 → 推送各阶段结果 ──
                if ev["type"] == "__session__":
                    session: TranslationSession = ev["session"]
                    _sessions[session.session_id] = session

                    # 若发生过降级/错误，先推送 error 事件（前端据此展示警告）
                    if session.errors or session.degradation_level:
                        yield {"event": "error", "data": json.dumps({
                            "code": "degraded",
                            "message": (session.errors[-1] if session.errors
                                        else "部分环节降级处理"),
                            "degradation_level": (
                                session.degradation_level.value
                                if session.degradation_level else None),
                        }, ensure_ascii=False)}

                    # 推送译前结果详情
                    if session.pre_translate_result:
                        st = session.pre_translate_result.strategy_book
                        if st:
                            yield {"event": "strategy", "data": json.dumps({
                                "ict_domain": st.ict_domain,
                                "difficulty": st.difficulty,
                                "style": st.style,
                                "literal_ratio": st.literal_ratio,
                            }, ensure_ascii=False)}

                        tt = session.pre_translate_result.term_table
                        if tt:
                            yield {"event": "terms", "data": json.dumps({
                                "total_terms": tt.total_count,
                                "rag_hit": tt.rag_hit_count,
                                "web_search": tt.web_search_count,
                                "pending": len(tt.pending_entries),
                            }, ensure_ascii=False)}

                    # 推送终稿
                    if session.final_text_restored:
                        yield {"event": "final", "data": json.dumps({
                            "final_text": session.final_text_restored,
                            "session_id": session.session_id,
                        }, ensure_ascii=False)}

                    # 推送质检报告
                    if (session.post_translate_result
                            and session.post_translate_result.qa_report):
                        qa = session.post_translate_result.qa_report
                        yield {"event": "qa", "data": json.dumps(
                            qa.to_dict(), ensure_ascii=False)}

                    # 推送进化报告
                    if session.evolution_report:
                        yield {"event": "evolution", "data": json.dumps(
                            session.evolution_report.to_dict(),
                            ensure_ascii=False)}

                    # 完成
                    yield {"event": "done", "data": json.dumps({
                        "session_id": session.session_id,
                        "elapsed_seconds": session.elapsed_seconds(),
                        "export_formats": ["docx", "html", "bilingual"],
                    }, ensure_ascii=False)}
                    break

                # ── 术语确认断点：等待用户确认 ──
                elif ev["type"] == "__terms_pending__":
                    yield {"event": "terms_pending", "data": json.dumps({
                        "session_id": ev["session_id"],
                        "pending_terms": ev["terms"],
                    }, ensure_ascii=False)}

                # ── 翻译异常终止 ──
                elif ev["type"] == "__error__":
                    yield {"event": "error", "data": json.dumps({
                        "code": "translation_failed",
                        "message": ev["message"],
                    }, ensure_ascii=False)}
                    break

                # ── 实时进度事件 ──
                else:
                    yield {"event": "progress", "data": json.dumps(
                        ev, ensure_ascii=False)}

        finally:
            # 清理后台任务
            if not task.done():
                task.cancel()

    return EventSourceResponse(event_stream())


# ── 术语确认 ───────────────────────────────────────────────────────

@app.post("/api/confirm_terms")
async def confirm_terms(session_id: str = Form(...),
                        confirmed_terms: str = Form("[]")):
    """用户确认低置信度术语：唤醒暂停的翻译任务并应用确认结果。

    请求体：confirmed_terms 为 JSON 字符串，数组项含
    {term, translation, action, confidence, ...}。
    """
    req = _confirm_requests.get(session_id)
    if not req:
        # 没有待确认的断点（可能已确认或超时）
        session = _sessions.get(session_id)
        if session:
            return {"accepted": False, "count": 0,
                    "message": "该会话当前没有待确认的术语"}
        return {"error": "会话不存在"}

    try:
        terms = json.loads(confirmed_terms)
    except json.JSONDecodeError:
        return {"error": "confirmed_terms 不是合法 JSON"}

    if not isinstance(terms, list):
        return {"error": "confirmed_terms 应为数组"}

    # 唤醒翻译任务（返回确认结果，orchestrator 会应用并继续）
    req["result"] = terms
    req["event"].set()
    return {"accepted": True, "count": len(terms)}


# ── 导出 ───────────────────────────────────────────────────────────

@app.get("/api/export/{session_id}")
async def export(session_id: str, format: str = "docx"):
    """下载导出文件"""
    session = _sessions.get(session_id)
    if not session:
        return {"error": "会话不存在"}

    if not session.final_text_restored:
        return {"error": "翻译尚未完成"}

    cfg = get_config().app
    output_path = export_to_format(
        session.final_text_restored, format, cfg.assets_dir
    )

    media_types = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "html": "text/html",
        "bilingual": "text/markdown",
    }

    return FileResponse(
        output_path,
        media_type=media_types.get(format, "application/octet-stream"),
        filename=f"translated.{format}",
    )


# ── 进化数据 ───────────────────────────────────────────────────────

@app.get("/api/evolution/{user_id}")
async def evolution(user_id: str):
    """获取用户进化数据"""
    from transagent.backend.knowledge.rag_terms import get_term_count
    from transagent.backend.knowledge.tm_store import get_tm_count

    return {
        "user_id": user_id,
        "total_terms": get_term_count(user_id),
        "total_tm": get_tm_count(user_id),
        "total_translations": 0,  # 后续实现
        "avg_qa_score": 0.0,
    }


# ── 健康检查 ───────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0"}


# ── 辅助 ───────────────────────────────────────────────────────────

def _find_ext(file_id: str) -> str:
    """在workspace中找到文件的扩展名"""
    cfg = get_config().app
    for f in os.listdir(cfg.workspace_dir):
        if f.startswith(file_id):
            return os.path.splitext(f)[1]
    return ".txt"

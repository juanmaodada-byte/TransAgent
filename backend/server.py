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

@app.post("/api/translate")
async def translate(file_id: str = Form(...), user_id: str = Form("demo_user")):
    """启动翻译，SSE流式返回进度+结果"""

    cfg = get_config().app
    ext = _find_ext(file_id)
    file_path = os.path.join(cfg.workspace_dir, f"{file_id}{ext}")

    if not os.path.exists(file_path):
        return {"error": f"文件不存在: {file_id}"}

    async def event_stream():
        session = None
        try:
            orchestrator = Orchestrator(user_id=user_id)

            async def on_progress(step: str, state: StepState, msg: str):
                """进度回调 → SSE event"""
                data = {
                    "type": "progress",
                    "step": step,
                    "state": state.value,
                    "message": msg,
                }
                yield {"event": "progress", "data": json.dumps(data, ensure_ascii=False)}

            session = await orchestrator.translate(
                file_path=file_path,
                on_progress=on_progress,
                on_terms_pending=None,  # Demo模式自动接受
            )

            _sessions[session.session_id] = session

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
            if session.post_translate_result and session.post_translate_result.qa_report:
                qa = session.post_translate_result.qa_report
                yield {"event": "qa", "data": json.dumps(qa.to_dict(), ensure_ascii=False)}

            # 推送进化报告
            if session.evolution_report:
                yield {"event": "evolution", "data": json.dumps(
                    session.evolution_report.to_dict(), ensure_ascii=False)}

            # 完成
            yield {"event": "done", "data": json.dumps({
                "session_id": session.session_id,
                "elapsed_seconds": session.elapsed_seconds(),
                "export_formats": ["docx", "html", "bilingual"],
            }, ensure_ascii=False)}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({
                "code": "translation_failed",
                "message": str(e),
            }, ensure_ascii=False)}

    return EventSourceResponse(event_stream())


# ── 术语确认 ───────────────────────────────────────────────────────

@app.post("/api/confirm_terms")
async def confirm_terms(session_id: str = Form(...),
                        confirmed_terms: str = Form("[]")):
    """用户确认低置信度术语（暂未实现完整的断点恢复·MVP阶段为预留接口）"""
    session = _sessions.get(session_id)
    if not session:
        return {"error": "会话不存在"}

    terms = json.loads(confirmed_terms)
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

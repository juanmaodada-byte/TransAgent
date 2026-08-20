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
from transagent.backend.pipeline.preprocess import detect_format, convert_to_md
from transagent.backend.pipeline.exporter import export_to_format
from transagent.backend.pipeline.restore import restore_placeholders

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
# D8.1 MVP：译中完成后的中英对照确认断点，等待 /api/confirm_draft 唤醒。
_draft_confirm_requests: dict[str, dict] = {}

# 术语确认等待超时（秒）·超时则自动接受并继续（前端断线等场景）
# 120s → 300s：给用户充足时间阅读术语卡片并确认（此前用户因耗时停滞错过确认）
CONFIRM_TIMEOUT_SECONDS = 300


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


@app.post("/api/convert_to_md")
async def convert_to_md_api(file: UploadFile = File(...)):
    """D8.1 MVP：独立文档转Markdown工具——用户上传文档，返回可复制的 MD 文本。

    输出为「结构解析前的原始 MD」：用户在翻译输入框粘贴 MD 后由主流程重新解析，
    避免把受保护占位符（{NT_n}）带进输入。
    """
    cfg = get_config().app
    os.makedirs(cfg.workspace_dir, exist_ok=True)
    import uuid
    file_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename or "unknown.txt")[1]
    file_path = os.path.join(cfg.workspace_dir, f"{file_id}{ext}")

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    try:
        fmt = detect_format(file_path)
        converted = convert_to_md(file_path, fmt.format_type)
        md = converted.md_text or ""
        max_chars = cfg.max_source_chars
        return {
            "md": md,
            "char_count": len(md),
            "over_limit": len(md) > max_chars,
            "limit": max_chars,
            "format": fmt.format_type,
            "warnings": list(converted.metadata.get("conversion_warnings", [])),
        }
    except Exception as e:
        return {"error": str(e)}


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

        async def on_draft_confirm(session, aligned_rows):
            """译中完成后的中英对照确认断点回调（D8.1 MVP）：
            推送中英对照 → 等待 /api/confirm_draft 唤醒。超时自动继续；会话被取消则抛错。"""
            req = {"event": asyncio.Event(), "result": None}
            _draft_confirm_requests[session.session_id] = req
            try:
                print(f"[Confirm] 会话 {session.session_id} 进入中英对照断点，{len(aligned_rows)} 句待确认")
                await queue.put({
                    "type": "__draft_pending__",
                    "session_id": session.session_id,
                    "rows": aligned_rows,
                })
                try:
                    await asyncio.wait_for(req["event"].wait(),
                                           timeout=CONFIRM_TIMEOUT_SECONDS)
                    print(f"[Confirm] 会话 {session.session_id} 中英对照已确认·继续译后")
                except asyncio.TimeoutError:
                    print(f"[Confirm] 会话 {session.session_id} 中英对照确认超时·自动继续")
            finally:
                _draft_confirm_requests.pop(session.session_id, None)

        async def run_translate():
            """后台运行翻译，完成后推送 session"""
            try:
                orchestrator = Orchestrator(user_id=user_id)
                session = await orchestrator.translate(
                    file_path=file_path,
                    on_progress=on_progress,
                    on_terms_pending=on_terms_pending,  # 完整闭环：暂停等待用户确认
                    on_draft_confirm=on_draft_confirm,  # D8.1 MVP：译中后中英对照确认
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

                    # 推送初稿。TranslateResult.draft 是译中产物，前端完成态三栏对照依赖它。
                    if session.translate_result and session.translate_result.draft:
                        draft_text = session.translate_result.draft
                        pmap = (session.preprocess_result.placeholder_map
                                if session.preprocess_result else None)
                        if pmap:
                            try:
                                draft_text = restore_placeholders(draft_text, pmap)
                            except Exception:
                                # 初稿展示不能影响终稿交付；还原失败时退回含占位符版本。
                                pass
                        yield {"event": "draft", "data": json.dumps({
                            "chunk_id": "draft",
                            "text_chunk": draft_text,
                        }, ensure_ascii=False)}

                    # 推送终稿（附带三栏句对齐：源句|初译句|终译句）
                    if session.final_text_restored:
                        aligned_rows = []
                        try:
                            from transagent.backend.pipeline.aligner import build_triple_alignment
                            pmap = (session.preprocess_result.placeholder_map
                                    if session.preprocess_result else None)
                            # 源文还原（占位符 → 原文）
                            src = session.preprocess_result.protected_md if session.preprocess_result else ""
                            if pmap:
                                src = restore_placeholders(src, pmap)
                            # 初译还原（占位符 → 译文）
                            draft = session.translate_result.draft if session.translate_result else ""
                            if pmap and draft:
                                draft = restore_placeholders(draft, pmap)
                            aligned_rows = build_triple_alignment(
                                src, draft, session.final_text_restored)
                        except Exception as e:
                            print(f"[Server] 三栏句对齐失败: {e}")
                            aligned_rows = []
                        yield {"event": "final", "data": json.dumps({
                            "final_text": session.final_text_restored,
                            "session_id": session.session_id,
                            "aligned_rows": aligned_rows,
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
                        "export_formats": ["md", "docx", "html", "bilingual"],
                    }, ensure_ascii=False)}
                    break

                # ── 术语确认断点：等待用户确认 ──
                elif ev["type"] == "__terms_pending__":
                    yield {"event": "terms_pending", "data": json.dumps({
                        "session_id": ev["session_id"],
                        "pending_terms": ev["terms"],
                    }, ensure_ascii=False)}

                # ── 中英对照确认断点（D8.1 MVP）：等待用户确认 ──
                elif ev["type"] == "__draft_pending__":
                    yield {"event": "draft_pending", "data": json.dumps({
                        "session_id": ev["session_id"],
                        "rows": ev["rows"],
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


@app.post("/api/confirm_draft")
async def confirm_draft(session_id: str = Form(...),
                        confirmed: str = Form("true")):
    """D8.1 MVP：用户确认译中初译（中英对照），唤醒暂停的翻译任务继续译后。"""
    req = _draft_confirm_requests.get(session_id)
    if not req:
        session = _sessions.get(session_id)
        if session:
            return {"accepted": False, "count": 0,
                    "message": "该会话当前没有待确认的中英对照"}
        return {"error": "会话不存在"}
    req["result"] = (confirmed or "").lower() in ("true", "1", "yes")
    req["event"].set()
    return {"accepted": True}


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
    pmap = (session.preprocess_result.placeholder_map
            if session.preprocess_result else None)
    output_path = export_to_format(
        session.final_text_restored, format, cfg.assets_dir,
        aligned_pairs=session.aligned_pairs,
        placeholder_map=pmap,
    )

    media_types = {
        "md": "text/markdown",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "html": "text/html",
        # D6：双语对照也导出为docx（源文/译文左右对照表格）
        "bilingual": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    filename = "bilingual.docx" if format == "bilingual" else f"translated.{format}"

    return FileResponse(
        output_path,
        media_type=media_types.get(format, "application/octet-stream"),
        filename=filename,
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


# ── 设置（LLM API 配置）────────────────────────────────────────────

@app.get("/api/settings/llm")
async def get_llm_settings():
    """获取 LLM API 配置（脱敏：密钥只回显掩码）。"""
    from transagent.backend.config import llm_settings_payload
    return llm_settings_payload()


@app.post("/api/settings/llm")
async def update_llm_settings(payload: dict):
    """保存 LLM API 配置。

    请求体: {"primary": {"provider","model","api_key","base_url"},
             "backup": {...}}
    api_key 为空时保留现有密钥；保存后写入 data/user_llm.json，重启仍生效。
    """
    from transagent.backend.config import apply_llm_settings, llm_settings_payload
    try:
        primary = payload.get("primary") or {}
        backup = payload.get("backup") or {}
        if not isinstance(primary, dict) or not isinstance(backup, dict):
            return {"error": "参数格式错误：primary/backup 应为对象"}
        apply_llm_settings(primary, backup)
        return llm_settings_payload()
    except Exception as e:
        return {"error": f"保存失败: {e}"}


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

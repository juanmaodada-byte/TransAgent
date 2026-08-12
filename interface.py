"""
TransAgent 统一数据契约
========================
v1.0 | 2026-08-06 | D1 锁定

本文件定义所有模块间的共享数据结构。
每个模块只依赖这个文件，不直接依赖其他模块的实现。

模块归属：
  - 全局共享: 本文件所有 dataclass
  - Vibe Coder A: backend/core/     (翻译核心·主Agent+Sub-Agent+LLM)
  - Vibe Coder B: backend/pipeline/ (文档管线·预处理+导出)
  - 成员 C:       backend/knowledge/(知识库·RAG+TM+偏好)
  - 成员 D:       frontend/         (React前端)

修改规则：
  1. D1-D2: 可自由修改，需通知全员
  2. D3 起: 只能新增字段（向后兼容），不能删除/重命名
  3. 任何修改必须同步更新本文件末尾的 changelog
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid


# ══════════════════════════════════════════════════════════════════
# 一、基础枚举
# ══════════════════════════════════════════════════════════════════

class StepState(str, Enum):
    """翻译流程步骤状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_USER = "waiting_user"


class DegradationLevel(str, Enum):
    """异常降级等级"""
    L0 = "silent_retry"     # 静默重试（用户无感知）
    L1 = "degraded"         # 降级继续（跳过非致命环节）
    L2 = "need_user"        # 暂停等待用户决策
    L3 = "meltdown"         # 翻译中止


class Confidence(str, Enum):
    """术语置信度"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TermAction(str, Enum):
    """术语翻译行为"""
    TRANSLATE = "translate"        # 正常翻译
    NOTRANSLATE = "notranslate"    # 保留原文不译


class TermSource(str, Enum):
    """术语来源"""
    RAG_HIT = "RAG命中"
    WEB_SEARCH = "Web搜索"
    LLM_GEN = "LLM生成"
    USER_CONFIRMED = "用户确认"
    WHITELIST = "白名单"


class FormatType(str, Enum):
    """输入文件格式"""
    MARKDOWN = "md"
    DOCX = "docx"
    PDF = "pdf"
    TEXT = "text"
    IMAGE = "image"


# ══════════════════════════════════════════════════════════════════
# 二、输入处理相关
# ══════════════════════════════════════════════════════════════════

@dataclass
class FormatResult:
    """文件格式检测结果。Vibe Coder B 产出。"""
    format_type: str = ""          # "md" | "docx" | "pdf" | "text" | "image"
    mime_type: str = ""
    size_bytes: int = 0
    page_count: Optional[int] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ConvertResult:
    """格式转换结果（任何格式→MD）。Vibe Coder B 产出。"""
    md_text: str = ""              # 转换后的MD结构化文本（纯字符串·无二进制）
    assets_dir: str = ""           # 提取的图片/资源目录路径
    image_count: int = 0
    metadata: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════
# 三、结构解析与分块相关（Vibe Coder B 产出）
# ══════════════════════════════════════════════════════════════════

@dataclass
class PlaceholderMap:
    """两类占位符映射表。Vibe Coder B 的结构解析器产出。"""
    nt_map: dict = field(default_factory=dict)    # {"{NT_0}": "原文", ...}  不可译·还原原文
    t_map: dict = field(default_factory=dict)     # {"{T_0}": "原文", ...}   需翻译·还原译文
    nt_count: int = 0
    t_count: int = 0

    def to_dict(self) -> dict:
        return {
            "nt_map": self.nt_map,
            "t_map": self.t_map,
            "nt_count": self.nt_count,
            "t_count": self.t_count,
        }


@dataclass
class Chunk:
    """文档分块。Vibe Coder B 的分块器产出。"""
    chunk_id: str = ""
    source_text: str = ""          # 受保护MD文本（包含{NT_n}和{T_n}占位符）
    token_estimate: int = 0
    heading_path: list = field(default_factory=list)  # 标题路径 ["## 概述", "### 安装"]
    order: int = 0                 # 在原文档中的序号


@dataclass
class PreprocessResult:
    """预处理完整产出。Vibe Coder B 交付给 Vibe Coder A。"""
    protected_md: str = ""         # 受保护MD（全文·含占位符）
    chunks: list = field(default_factory=list)   # list[Chunk]
    placeholder_map: Optional[PlaceholderMap] = None
    token_estimate_total: int = 0
    chunk_count: int = 0


# ══════════════════════════════════════════════════════════════════
# 四、术语与知识库相关（成员 C 接口 + Vibe Coder A 消费）
# ══════════════════════════════════════════════════════════════════

@dataclass
class TermEntry:
    """单条术语。成员 C 的RAG术语库存储格式 & Vibe Coder A 使用格式。"""
    term: str = ""                 # 源术语（英文）
    translation: str = ""          # 目标译法（中文）
    domain: str = ""               # ICT子领域标签 "Kubernetes/云原生"
    confidence: str = "medium"     # "high" | "medium" | "low"
    action: str = "translate"      # "translate" | "notranslate"
    source: str = ""               # "RAG命中" | "Web搜索" | "LLM生成" | "用户确认" | "白名单"
    user_id: str = ""              # 个人化：关联用户ID
    timestamp: str = ""            # ISO格式时间戳

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "translation": self.translation,
            "domain": self.domain,
            "confidence": self.confidence,
            "action": self.action,
            "source": self.source,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TermEntry":
        return cls(**{k: d.get(k, "") for k in [
            "term", "translation", "domain", "confidence", "action", "source", "user_id", "timestamp"
        ]})


@dataclass
class TermTable:
    """项目术语表。Vibe Coder A 翻译阶段使用的当前项目术语清单。"""
    entries: list = field(default_factory=list)    # list[TermEntry]
    pending_entries: list = field(default_factory=list)  # list[TermEntry]  低置信度·待用户确认
    total_count: int = 0
    rag_hit_count: int = 0
    web_search_count: int = 0
    llm_gen_count: int = 0

    def to_list(self) -> list[dict]:
        return [e.to_dict() for e in self.entries]


@dataclass
class TMEntry:
    """单条翻译记忆。成员 C 的TM库存储格式 & Vibe Coder A 使用格式。"""
    source_seg: str = ""           # 源文句段
    target_seg: str = ""           # 译文句段
    quality_score: float = 0.0     # 质检评分（0-10）
    similarity: float = 0.0        # 与当前源文的相似度（0-1）
    domain: str = ""               # ICT子领域
    user_id: str = ""              # 个人化
    timestamp: str = ""            # ISO格式

    def to_dict(self) -> dict:
        return {
            "source_seg": self.source_seg,
            "target_seg": self.target_seg,
            "quality_score": self.quality_score,
            "similarity": self.similarity,
            "domain": self.domain,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
        }


# ══════════════════════════════════════════════════════════════════
# 五、翻译策略书（Vibe Coder A 译前Sub-Agent 产出）
# ══════════════════════════════════════════════════════════════════

@dataclass
class StrategyBook:
    """翻译策略书。译前Sub-Agent的策略制定LLM产出，传给译中+译后。"""
    ict_domain: str = ""           # ICT子领域标签 "Kubernetes/云原生"
    domain_confidence: str = "medium"
    difficulty: str = "medium"     # "easy" | "medium" | "hard"
    style: str = "technical"       # "technical" | "academic" | "blog"
    literal_ratio: float = 0.6     # 直译/意译比例（0=全意译，1=全直译）
    target_audience: str = "开发者"
    rules: dict = field(default_factory=lambda: {
        "code": "notranslate",
        "tone": "professional",
        "sentence_length": "medium",
        "voice": "active",
    })
    analysis_notes: str = ""         # 策略判断依据（D5技能化后LLM产出·此前被丢弃）
    direction: str = ""              # 翻译方向 "en_to_zh" | "zh_to_en"（D5目录化后新增·
                                     # 由策略技能从输入记录·译中主译/一致性以本字段路由方向）

    def to_dict(self) -> dict:
        return {
            "ict_domain": self.ict_domain,
            "domain_confidence": self.domain_confidence,
            "difficulty": self.difficulty,
            "style": self.style,
            "literal_ratio": self.literal_ratio,
            "target_audience": self.target_audience,
            "rules": self.rules,
            "analysis_notes": self.analysis_notes,
            "direction": self.direction,
        }


# ══════════════════════════════════════════════════════════════════
# 六、翻译阶段结果（Vibe Coder A 各 Sub-Agent 产出）
# ══════════════════════════════════════════════════════════════════

@dataclass
class PreTranslateResult:
    """译前Sub-Agent完整产出。"""
    chunks: list = field(default_factory=list)        # list[Chunk]
    strategy_book: Optional[StrategyBook] = None
    term_table: Optional[TermTable] = None
    placeholder_map: Optional[PlaceholderMap] = None


@dataclass
class ConsistencyReport:
    """一致性检查报告。译中Sub-Agent产出。"""
    precheck_passed: bool = True   # Python预检是否全部通过
    issues_found: int = 0          # 发现的不一致数量
    llm_fix_triggered: bool = False  # 是否触发了LLM修复
    details: list = field(default_factory=list)


@dataclass
class TranslateResult:
    """译中Sub-Agent完整产出。"""
    draft: str = ""                # 初译稿MD文本（含占位符）
    consistency_report: Optional[ConsistencyReport] = None
    tm_refs_used: int = 0          # 实际使用的TM参考数量


@dataclass
class QAIssue:
    """质检问题。译后Sub-Agent的质检LLM产出。"""
    location: str = ""             # "chunk_1 段落3"
    severity: str = "minor"        # "critical" | "major" | "minor"
    type: str = ""                 # "漏译" | "术语错误" | "翻译腔" | "代码误译" | "风格偏差"
    description: str = ""


@dataclass
class QAResult:
    """质检报告。译后Sub-Agent的质检LLM产出。"""
    total_score: float = 0.0       # 0-10
    term_accuracy: float = 0.0     # 术语准确性 30%
    semantic_fidelity: float = 0.0 # 语义忠实度 30%
    code_integrity: float = 0.0    # 代码/参数完整性 15%
    fluency: float = 0.0           # 流畅性 15%
    style_match: float = 0.0       # 风格匹配度 10%
    issues: list = field(default_factory=list)   # list[QAIssue]
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "total_score": self.total_score,
            "term_accuracy": self.term_accuracy,
            "semantic_fidelity": self.semantic_fidelity,
            "code_integrity": self.code_integrity,
            "fluency": self.fluency,
            "style_match": self.style_match,
            "issues": [{"location": i.location, "severity": i.severity,
                        "type": i.type, "description": i.description}
                       for i in self.issues],
            "summary": self.summary,
        }


@dataclass
class PostTranslateResult:
    """译后Sub-Agent完整产出。"""
    final_text: str = ""           # 终稿MD文本（含占位符·未还原）
    qa_report: Optional[QAResult] = None
    polish_notes: str = ""         # 润色说明


# ══════════════════════════════════════════════════════════════════
# 七、学习与进化相关
# ══════════════════════════════════════════════════════════════════

@dataclass
class AlignedPair:
    """句级对齐结果。Vibe Coder B 的aligner产出。"""
    source_seg: str = ""
    target_seg: str = ""
    alignment_score: float = 0.0   # 对齐置信度 0-1
    chunk_id: str = ""


@dataclass
class EvolutionReport:
    """进化报告。每次翻译完成后生成。"""
    new_terms_count: int = 0       # 本次新增术语
    new_tm_count: int = 0          # 本次新增TM句对
    total_terms: int = 0           # 术语库累计
    total_tm: int = 0              # TM累计
    tm_reuse_rate: float = 0.0     # 本次TM复用率
    rag_hit_rate: float = 0.0      # 本次术语RAG命中率
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "new_terms_count": self.new_terms_count,
            "new_tm_count": self.new_tm_count,
            "total_terms": self.total_terms,
            "total_tm": self.total_tm,
            "tm_reuse_rate": self.tm_reuse_rate,
            "rag_hit_rate": self.rag_hit_rate,
            "summary": self.summary,
        }


# ══════════════════════════════════════════════════════════════════
# 八、用户偏好（成员 C 接口）
# ══════════════════════════════════════════════════════════════════

@dataclass
class UserPrefs:
    """用户偏好Profile。成员C存储，Vibe Coder A读取。"""
    user_id: str = ""
    default_style: str = "technical"
    domain_tags: list = field(default_factory=list)    # 常用ICT子领域
    term_preferences: dict = field(default_factory=dict)  # {term: preferred_translation}
    strategy_history: list = field(default_factory=list)   # 最近策略选择
    literal_ratio: float = 0.6
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "default_style": self.default_style,
            "domain_tags": self.domain_tags,
            "term_preferences": self.term_preferences,
            "strategy_history": self.strategy_history,
            "literal_ratio": self.literal_ratio,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ══════════════════════════════════════════════════════════════════
# 九、翻译会话（全流程状态容器）
# ══════════════════════════════════════════════════════════════════

@dataclass
class TranslationSession:
    """一次翻译任务的完整状态。Vibe Coder A的主Agent编排器管理。"""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    user_id: str = ""
    file_path: str = ""
    target_format: str = ""        # 用户选择的导出格式

    # ── 步骤状态 ──
    steps: dict = field(default_factory=lambda: {
        "input_detect": StepState.PENDING,
        "input_convert": StepState.PENDING,
        "pre_translate": StepState.PENDING,
        "terminology_confirm": StepState.PENDING,
        "translate": StepState.PENDING,
        "post_translate": StepState.PENDING,
        "restore": StepState.PENDING,
        "align": StepState.PENDING,
        "learn": StepState.PENDING,
        "export": StepState.PENDING,
    })

    # ── 预处理数据（Vibe Coder B → A）──
    format_result: Optional[FormatResult] = None
    convert_result: Optional[ConvertResult] = None
    preprocess_result: Optional[PreprocessResult] = None
    user_prefs: Optional[UserPrefs] = None

    # ── 译前 ──
    pre_translate_result: Optional[PreTranslateResult] = None
    pending_terms: list = field(default_factory=list)   # 待用户确认的术语

    # ── 译中 ──
    translate_result: Optional[TranslateResult] = None

    # ── 译后 ──
    post_translate_result: Optional[PostTranslateResult] = None

    # ── 交付 ──
    final_text_restored: str = ""     # 占位符还原后的最终文本
    aligned_pairs: list = field(default_factory=list)   # list[AlignedPair]

    # ── 学习 ──
    evolution_report: Optional[EvolutionReport] = None

    # ── 导出 ──
    export_path: str = ""

    # ── 计时与错误 ──
    started_at: float = 0.0
    completed_at: float = 0.0
    errors: list = field(default_factory=list)         # 错误日志
    degradation_level: Optional[DegradationLevel] = None

    def elapsed_seconds(self) -> float:
        """已耗时（秒）"""
        import time
        if self.completed_at:
            return self.completed_at - self.started_at
        return time.time() - self.started_at

    def to_progress_dict(self) -> dict:
        """给前端SSE推送的进度快照"""
        completed = sum(1 for s in self.steps.values() if s == StepState.COMPLETED)
        total = len(self.steps)
        return {
            "session_id": self.session_id,
            "progress_pct": int(completed / total * 100),
            "steps": {k: v.value for k, v in self.steps.items()},
            "elapsed_seconds": self.elapsed_seconds(),
            "degradation_level": self.degradation_level.value if self.degradation_level else None,
        }


# ══════════════════════════════════════════════════════════════════
# 十、前后端 API 契约
# ══════════════════════════════════════════════════════════════════

# ── POST /api/upload ──────────────────────────────────────────────
# Request: multipart/form-data { file: binary }
# Response:
UPLOAD_RESPONSE_SCHEMA = {
    "file_id": "string",
    "format": "md|docx|pdf|text|image",
    "filename": "original_name.docx",
    "size_kb": 156,
    "page_count": None,          # PDF时有值
    "md_preview": "前500字符预览",
}

# ── POST /api/translate ───────────────────────────────────────────
# Request: JSON { file_id, user_id, target_format? }
# Response: SSE 流
#   事件类型:
#     progress  → { step, state, message, progress_pct }
#     terms     → { pending_terms: [...] }                      # 低置信度术语·等用户确认
#     strategy  → { ict_domain, difficulty, style, ... }        # 策略书展示
#     draft     → { chunk_id, text_chunk }                      # 流式初译稿（逐chunk）
#     qa        → { total_score, term_accuracy, ..., issues }   # 质检报告
#     final     → { final_text }                                # 终稿（流式输出）
#     evolution → { new_terms_count, total_terms, ... }         # 进化报告
#     error     → { code, message, degradation_level }
#     done      → { session_id, export_formats: ["docx","html","bilingual"] }

# ── POST /api/confirm_terms ───────────────────────────────────────
# Request: JSON { session_id, confirmed_terms: [{term, translation, action}] }
# Response: { accepted: true, count: 3 }

# ── GET /api/export/{session_id}?format=docx|html|bilingual ──────
# Response: binary file download

# ── GET /api/evolution/{user_id} ──────────────────────────────────
# Response:
EVOLUTION_RESPONSE_SCHEMA = {
    "user_id": "string",
    "total_terms": 156,
    "total_tm": 820,
    "total_translations": 23,
    "avg_qa_score": 9.1,
    "rag_hit_rate_trend": [0.2, 0.45, 0.68, 0.82, 0.88],  # 最近5次
    "tm_reuse_trend": [0.0, 0.15, 0.35, 0.52, 0.61],
}


# ══════════════════════════════════════════════════════════════════
# 十一、模块接口函数签名（各模块暴露的方法）
# ══════════════════════════════════════════════════════════════════

# Vibe Coder B — backend/pipeline/
# ────────────────────────────────
# detect_format(file_path: str) -> FormatResult
# convert_to_md(file_path: str, format_type: str) -> ConvertResult
# parse_structure(md_text: str) -> tuple[str, PlaceholderMap]   # (protected_md, pmap)
# chunk_document(protected_md: str, max_tokens: int = 30000) -> list[Chunk]
# preprocess(file_path: str) -> PreprocessResult                # 一站式预处理入口
# restore_placeholders(md_text: str, pmap: PlaceholderMap) -> str
# align_sentences(source_md: str, target_md: str) -> list[AlignedPair]
# export_to_format(md_text: str, target_format: str, assets_dir: str) -> str  # 返回导出路径

# Vibe Coder A — backend/core/
# ────────────────────────────
# async spawn_pre_translate(preprocess: PreprocessResult, user_prefs: UserPrefs) -> PreTranslateResult
# async spawn_translate(chunks: list[Chunk], term_table: TermTable,
#                        strategy_book: StrategyBook, tm_refs: list[TMEntry]) -> TranslateResult
# async spawn_post_translate(source_md: str, draft: str,
#                             term_table: TermTable, strategy_book: StrategyBook) -> PostTranslateResult
# async translate_document(file_path: str, user_id: str,
#                           on_progress: Callable) -> TranslationSession  # 主入口

# 成员 C — backend/knowledge/
# ───────────────────────────
# def search_rag(term: str, user_id: str, domain: str = "", top_k: int = 3) -> list[TermEntry]
# def write_rag_terms(terms: list[TermEntry]) -> int           # 返回写入条数
# def search_tm(source_text: str, user_id: str, threshold: float = 0.85) -> list[TMEntry]
# def write_tm_entries(entries: list[TMEntry]) -> int          # 返回写入条数
# def load_user_prefs(user_id: str) -> UserPrefs
# def save_user_prefs(prefs: UserPrefs) -> None
# def get_evolution_stats(user_id: str) -> EvolutionReport


# ══════════════════════════════════════════════════════════════════
# 变更记录
# ══════════════════════════════════════════════════════════════════
# v1.0 | 2026-08-06 | D1 初始版本，锁定全部数据契约
# v1.1 | 2026-08-07 | D2 Vibe Coder A: 新增 agent_framework.py（Sub-Agent调用框架·内部模块）
#                         BaseAgent/AgentContext/AgentResult/spawn/spawn_parallel 均为 core/ 内部实现
#                         接口契-约本身无变更，三个 Sub-Agent 的 spawn_* 函数签名保持向后兼容

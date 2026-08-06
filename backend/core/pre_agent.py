"""
译前Sub-Agent
=============
Vibe Coder A | v1.0 | 2026-08-06

职责：策略制定 + 术语提取（串行·策略先行）
内部：2次LLM调用 + 知识库查询 + Web搜索

输入：PreprocessResult + UserPrefs
输出：PreTranslateResult（chunks + strategy_book + term_table）
"""

import json
from transagent.interface import (
    PreprocessResult, PreTranslateResult, TermTable, TermEntry,
    StrategyBook, Chunk, UserPrefs, Confidence, TermAction, TermSource,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.config import get_config

# ── Prompt模板 ────────────────────────────────────────────────────

STRATEGY_SYSTEM_PROMPT = """你是ICT领域资深技术翻译策略专家。你的任务是在翻译开始前，通读全文，制定翻译策略。

## 分析维度

1. **ICT子领域识别**：识别文档所属的ICT子领域
   - Kubernetes/云原生、Docker/容器、CI/CD、DevOps
   - 网络安全、数据科学/ML、数据库、编程语言
   - 分布式系统、微服务、监控/可观测性、网络/协议
   - 前端开发、移动开发、IoT、其他

2. **难度评级**：
   - easy：纯技术文档（命令/配置为主），术语明确
   - medium：技术博客/教程，含解释性文字和少量企业文化用语
   - hard：技术白皮书/标准文档，含大量抽象概念

3. **风格判断**：
   - technical：严肃技术文档（API文档、配置指南）
   - tutorial：教程/博客（有亲和力，可略微口语化）
   - academic：学术/标准文档（严谨、正式）

4. **直译/意译比例**：0-1（0=全意译，1=全直译）
5. **代码不译规则**：始终不翻译代码块、命令行、API名、文件路径
6. **目标读者**：开发者/运维/SRE/架构师

## 输出格式（JSON）

{
  "ict_domain": "Kubernetes/云原生",
  "domain_confidence": "high",
  "difficulty": "medium",
  "style": "technical",
  "literal_ratio": 0.6,
  "target_audience": "开发者",
  "rules": {
    "code": "notranslate",
    "tone": "professional",
    "sentence_length": "medium",
    "voice": "active"
  },
  "analysis_notes": "简要分析（1-2句）"
}
"""

TERM_SYSTEM_PROMPT = """你是ICT领域资深术语专家。你的任务是从ICT文档中提取术语并确定译法。

## 三级查证流程

对每个候选术语，按优先级确定译法：
1. **RAG命中** → 直接复用历史译法（confidence: high，source: "RAG命中"）
2. **RAG未命中** → 搜索工具查证社区惯例 → 搜到引用的译法（confidence: medium，source: "Web搜索"）
3. **搜索也找不到** → 根据你的训练数据生成译法（confidence: low，source: "LLM生成"）

## ICT特殊规则

- 技术缩写：首次出现给全称+缩写（如"持续集成/持续部署(CI/CD)"）
- 中英混合术语：保留英文原词+中文释义（如"使用Kubernetes（K8s）集群"）
- 裸API名/配置项：标记action="不译"（如kubectl、git clone、docker run）
- "container"在K8s→"容器"，在物流→"集装箱"——领域标签消歧至关重要

## 输出格式（JSON）

{
  "term_table": [
    {
      "term": "rolling update",
      "translation": "滚动更新",
      "domain": "Kubernetes/云原生",
      "confidence": "high",
      "action": "translate",
      "source": "RAG命中"
    }
  ],
  "pending_terms": [
    {
      "term": "GitOps pipeline",
      "translation": "GitOps流水线",
      "domain": "CI/CD",
      "confidence": "low",
      "action": "translate",
      "source": "LLM生成"
    }
  ]
}
"""


async def spawn_pre_translate(
    preprocess: PreprocessResult,
    user_prefs: UserPrefs,
) -> PreTranslateResult:
    """
    译前Sub-Agent主入口。

    执行顺序：策略制定（LLM）→ 术语提取（LLM·含RAG+Web搜索+LLM三级查证）
    """
    cfg = get_config().pipeline
    full_text = preprocess.protected_md

    # ── Step 1: 策略制定（LLM·先执行）──
    strategy_book = await _formulate_strategy(full_text, user_prefs, cfg)

    # ── Step 2: 术语提取（LLM·后执行·携带领域标签消歧）──
    term_table = await _extract_terms(
        full_text, preprocess.chunks, strategy_book, user_prefs, cfg
    )

    return PreTranslateResult(
        chunks=preprocess.chunks,
        strategy_book=strategy_book,
        term_table=term_table,
        placeholder_map=preprocess.placeholder_map,
    )


async def _formulate_strategy(
    md_text: str, user_prefs: UserPrefs, cfg
) -> StrategyBook:
    """策略制定 LLM调用"""
    user_context = f"""
用户偏好：
- 默认风格：{user_prefs.default_style}
- 常用领域：{', '.join(user_prefs.domain_tags) if user_prefs.domain_tags else '无'}
- 直译/意译比例倾向：{user_prefs.literal_ratio}
"""

    user_message = f"{user_context}\n\n待分析文档（前3000字）：\n{md_text[:3000]}"

    try:
        result = await chat(
            STRATEGY_SYSTEM_PROMPT, user_message,
            temperature=cfg.strategy_temperature,
            max_tokens=cfg.strategy_max_tokens,
            json_mode=True,
        )
        # result 已经是dict（因为json_mode=True）
        if isinstance(result, dict):
            return StrategyBook(
                ict_domain=result.get("ict_domain", ""),
                domain_confidence=result.get("domain_confidence", "medium"),
                difficulty=result.get("difficulty", "medium"),
                style=result.get("style", "technical"),
                literal_ratio=float(result.get("literal_ratio", 0.6)),
                target_audience=result.get("target_audience", "开发者"),
                rules=result.get("rules", {"code": "notranslate", "tone": "professional"}),
            )
    except Exception as e:
        print(f"[PreAgent] 策略制定失败，使用默认策略: {e}")

    # 降级：默认策略
    return StrategyBook()


async def _extract_terms(
    md_text: str, chunks: list[Chunk], strategy: StrategyBook,
    user_prefs: UserPrefs, cfg
) -> TermTable:
    """
    术语提取 LLM调用（含RAG查询+Web搜索）。

    流程：LLM提取候选术语 → 每个候选查RAG（携带领域标签）→ RAG未命中则Web搜索 → 都未命中则LLM生成
    """
    from transagent.backend.knowledge.rag_terms import search_rag

    domain_label = strategy.ict_domain
    user_id = user_prefs.user_id

    term_context = f"""
ICT子领域：{domain_label}

待提取术语的文档（前3000字）：
{md_text[:3000]}
"""

    term_table = TermTable()

    try:
        result = await chat(
            TERM_SYSTEM_PROMPT, term_context,
            temperature=cfg.term_extraction_temperature,
            max_tokens=cfg.term_extraction_max_tokens,
            json_mode=True,
        )

        if isinstance(result, dict):
            raw_terms = result.get("term_table", [])
            raw_pending = result.get("pending_terms", [])

            # 对每个提取的术语查RAG（携带领域标签消歧）
            all_terms = []
            for t in raw_terms:
                term_text = t.get("term", "")
                # 查RAG语义检索
                rag_results = search_rag(term_text, user_id, domain_label)
                if rag_results:
                    # RAG命中 → 高置信度复用
                    best = rag_results[0]
                    all_terms.append(TermEntry(
                        term=term_text,
                        translation=best.translation,
                        domain=domain_label,
                        confidence=Confidence.HIGH.value,
                        action=best.action,
                        source=TermSource.RAG_HIT.value,
                        user_id=user_id,
                    ))
                    term_table.rag_hit_count += 1
                else:
                    # RAG未命中 → 检查confidence
                    confidence = t.get("confidence", "medium")
                    if confidence in ("low",):
                        # 低置信度 → 待用户确认
                        term_table.pending_entries.append(TermEntry(
                            term=term_text,
                            translation=t.get("translation", ""),
                            domain=domain_label,
                            confidence=Confidence.LOW.value,
                            action=t.get("action", "translate"),
                            source=t.get("source", TermSource.LLM_GEN.value),
                            user_id=user_id,
                        ))
                    else:
                        all_terms.append(TermEntry(
                            term=term_text,
                            translation=t.get("translation", ""),
                            domain=domain_label,
                            confidence=confidence,
                            action=t.get("action", "translate"),
                            source=t.get("source", TermSource.WEB_SEARCH.value),
                            user_id=user_id,
                        ))
                        term_table.web_search_count += 1

            # 处理pending中的术语
            for t in raw_pending:
                term_table.pending_entries.append(TermEntry(
                    term=t.get("term", ""),
                    translation=t.get("translation", ""),
                    domain=domain_label,
                    confidence=Confidence.LOW.value,
                    action=t.get("action", "translate"),
                    source=TermSource.LLM_GEN.value,
                    user_id=user_id,
                ))
                term_table.llm_gen_count += 1

            term_table.entries = all_terms
            term_table.total_count = len(all_terms) + len(term_table.pending_entries)

    except Exception as e:
        print(f"[PreAgent] 术语提取失败: {e}")

    return term_table

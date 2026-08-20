"""
句级对齐
========
Vibe Coder B | v1.2 | 2026-08-20 (D8)

职责：源文↔译文句级自动对齐。
      确定性NLP算法（切句 + 长度/内容相似 DP 块对齐），不经过LLM。
      产出AlignedPair列表，用于三栏对照、TM写入和双语对照导出。

D6新增（共享池·质检结构化定位）：
  - align_chunks()：按chunk逐块对齐（源chunk ↔ 对应块译文），填充 chunk_id
    —— 供初译稿进池子时对齐，质检按句对定位问题
  - locate_quote()：把质检LLM摘抄的源/译句，模糊匹配到具体句对
    —— 定位最终由系统确定，LLM只负责摘抄指认

D8重写（修复"句对齐错行"）：
  原顺序对齐假设源/译一一对应且句数一致；译文一旦合并/拆分句子，
  顺序配对就整段错位（如源21句vs译6句→前6对全配错行）。
  现改为确定性DP块对齐：允许 1:1 / 1:2 / 2:1 / 2:2 合并块与 1:0 / 0:1
  跳过（高代价），块代价 = 长度比偏离文档整体中心比 + 内容重叠奖励。

  输出契约：每源句一行（行主键=源句，顺序保持）；合并块的目标文本挂在
  块首源句行上，块内后续源句行 target_seg 为空；缺译源句行 target 亦为空。
  —— 供 build_triple_alignment 按源句稳定拼行（两次对齐共用同一源句序列）。

使用：
    from transagent.backend.pipeline.aligner import align_sentences, align_chunks, locate_quote
    pairs = align_sentences(source_md, target_md)
    chunk_pairs = align_chunks(chunks, chunk_drafts)
    pair, index = locate_quote(chunk_pairs, source_quote, target_quote)
"""

import functools
import math
import re
import difflib
from collections import defaultdict, deque
from transagent.interface import AlignedPair


# ── DP 对齐参数 ────────────────────────────────────────────────────
_SKIP_COST = 2.5        # 1:0 / 0:1 跳过代价：正常合并块代价 <0.5，跳过被强制高代价
_MERGE_PENALTY = 0.2    # D9.2：合并块额外代价（原0.5·在语义对齐下过高压过正确2:1合并）
_OVERLAP_BONUS = 1.5    # 源词在译文中保留时的奖励系数（缓解纯长度歧义）
_HEADING_ALIGN_COST = 0.01      # 标题↔标题：结构性锚定，近零代价
_HEADING_MISMATCH_PENALTY = 1.5 # 标题与正文混排：重罚（防标题被并入正文块错行）
_DP_FULL_CELLS = 200_000  # m*n 低于此值走全量DP（精确），高于则退化为带状DP
_DP_BAND = 30             # 带状DP带宽：源/译句序漂移超该值视为病态文本
_SEM_BONUS = 3.0          # D9.2：语义相似度奖励系数（只对 sim>_SEM_FLOOR 的高置信匹配生效）
_SEM_FLOOR = 0.60         # D9.2：语义奖励下限——低于该值的错配不给奖励



def _semantic_sim_matrix(
    source_sents: list[str], target_sents: list[str],
) -> list[list[float]] | None:
    """bge-m3 跨语言嵌入 → 源×译余弦相似度矩阵（D9.2 语义对齐）。

    长度比对齐在译文长度失真（期刊头部/自由翻译）时漂移；语义相似度按"意思"配对
    不受影响。直接加载本地 bge-m3（绕过 rag 包的 import 环境问题·进程内缓存）。
    嵌入失败/无模型时返回 None → 调用方回退纯长度对齐。
    """
    try:
        model = _bge_m3()
        sv = model.encode(source_sents, normalize_embeddings=True)
        tv = model.encode(target_sents, normalize_embeddings=True)
        return [
            [float(sum(a * b for a, b in zip(s, t))) for t in tv]  # 归一化·点积=余弦
            for s in sv
        ]
    except Exception as e:
        print(f"[Aligner] 语义对齐不可用，回退长度对齐: {e}")
        return None


@functools.lru_cache(maxsize=1)
def _bge_m3():
    """进程内单例加载本地 bge-m3（与 RAG 同模型·跨语言句子嵌入）。"""
    import os
    from sentence_transformers import SentenceTransformer
    here = os.path.dirname(os.path.abspath(__file__))
    model_dir = os.path.normpath(
        os.path.join(here, "..", "knowledge", "rag", "rag", "models", "bge-m3"))
    if os.path.isdir(model_dir):
        return SentenceTransformer(model_dir, local_files_only=True)
    return SentenceTransformer("BAAI/bge-m3")


def align_sentences(source_md: str, target_md: str) -> list[AlignedPair]:
    """
    源文↔译文句级对齐（DP块对齐）。

    算法：
      1. 分割源文和译文为句子列表（去噪：跳过空/过短句）
      2. 以句子块为单位求最优对齐路径，允许 1:1 / 1:2 / 2:1 / 2:2 合并与
         1:0 / 0:1 跳过（跳过高代价，仅在句数严重失衡时使用）
      3. 块代价 = |ln(实际长度比 / 整体长度比中心)| - 内容重叠奖励
      4. 回溯重建：每个源句产出一行；合并块目标文本挂在块首源句行上

    Args:
        source_md: 源文MD文本
        target_md: 译文MD文本

    Returns:
        AlignedPair列表（行主键=源句，顺序保持；target_seg 可空）
    """
    source_sents = _clean_sentences(source_md)
    target_sents = _clean_sentences(target_md)
    # D9.2：期刊元数据行（Email/日期/ISSN/版权·无稳定长度比→DP错位并级联）两侧都不参与对齐
    source_sents = [s for s in source_sents if not _META_NOISE_RE.match(s)]
    target_sents = [s for s in target_sents if not _META_NOISE_RE.match(s)]
    if not source_sents:
        return []

    # 文档整体长度比中心：自适应语言对/文本密度（如 en→zh ~0.3，zh→en ~3）
    src_total = sum(len(s) for s in source_sents)
    tgt_total = sum(len(t) for t in target_sents)
    if tgt_total == 0:
        return [AlignedPair(source_seg=s, target_seg="", alignment_score=0.0)
                for s in source_sents]
    r_center = tgt_total / src_total

    # D9.2：语义相似度矩阵（长度比失真的兜底信号）·嵌入失败自动回退
    sim = _semantic_sim_matrix(source_sents, target_sents)
    blocks = _optimal_alignment(source_sents, target_sents, r_center, sim)

    pairs: list[AlignedPair] = []
    for src_idxs, tgt_idxs in blocks:
        if not src_idxs:
            continue                      # 0:1 纯插入译句 → 不产行
        src_text = source_sents[src_idxs[0]]
        tgt_text = " ".join(target_sents[t] for t in tgt_idxs).strip()
        score = _alignment_score(src_text, tgt_text) if tgt_text else 0.0
        pairs.append(AlignedPair(
            source_seg=src_text,
            target_seg=tgt_text,
            alignment_score=score,
        ))
        # 2:1/2:2 合并块：块首源句行挂全部译文，后续源句行 target 置空
        # （该源句的译文已并入上一行，避免译文重复/错位）
        for extra in src_idxs[1:]:
            pairs.append(AlignedPair(
                source_seg=source_sents[extra],
                target_seg="",
                alignment_score=0.0,
            ))
    return pairs


def _clean_sentences(text: str) -> list[str]:
    """切句 + 去噪（空/过短句丢弃）。源译共用同一过滤器 → 行主键一致。"""
    return [s.strip() for s in _split_sentences(text) if len(s.strip()) >= 5]


def _optimal_alignment(
    source_sents: list[str],
    target_sents: list[str],
    r_center: float,
    sim: list[list[float]] | None = None,
) -> list[tuple]:
    """
    DP求源/译句序列的最优块对齐路径。

    状态 (i, j)：源前i句 ↔ 译前j句对齐完毕的最小累计代价。
    转移：1:1 / 1:2 / 2:1 / 2:2（块代价）+ 1:0 / 0:1（跳过·固定高代价）。
    大文本退化为带状DP（|i-j|<=band）控制复杂度，band 至少=|m-n| 保证可达。

    Returns:
        有序块列表 [(源句下标元组, 译句下标元组), ...]
    """
    m, n = len(source_sents), len(target_sents)
    if m * n <= _DP_FULL_CELLS:
        band = max(m, n)
    else:
        band = max(_DP_BAND, abs(m - n))

    # 预计算每句元数据（长度/拉丁词集合/标题标记）——块代价走纯算术，大文档关键
    slen = [len(s) for s in source_sents]
    tlen = [len(t) for t in target_sents]
    sword = [set(_WORD_RE.findall(s.lower())) for s in source_sents]
    tword = [set(_WORD_RE.findall(t.lower())) for t in target_sents]
    shead = [_is_heading(s) for s in source_sents]
    thead = [_is_heading(t) for t in target_sents]

    def block_cost(si1: int, si2: int, ti1: int, ti2: int) -> float:
        """源句[si1..si2) ↔ 译句[ti1..ti2) 对齐代价（越小越优）。

        长度项：|ln(块长度比 / 整体长度比中心)|——1:1 正常句 ~0；错配合并块成倍偏离。
        内容项：源文拉丁词/数字若原样保留在译文中（术语、代码、数字）→ 奖励。
        标题锚：标题↔标题近零代价；标题与正文混排重罚（防标题被并入正文块错行）。
        """
        sl = tl = 0
        for k in range(si1, si2):
            sl += slen[k]
        for k in range(ti1, ti2):
            tl += tlen[k]
        if sl == 0 or tl == 0:
            return _SKIP_COST
        ratio = tl / sl
        if ratio <= 0 or r_center <= 0:
            return _SKIP_COST

        sh = th = False
        for k in range(si1, si2):
            sh = sh or shead[k]
        for k in range(ti1, ti2):
            th = th or thead[k]
        if sh or th:
            if sh and th and si2 - si1 == 1 and ti2 - ti1 == 1:
                return _HEADING_ALIGN_COST
            return _SKIP_COST + _HEADING_MISMATCH_PENALTY

        cost = abs(math.log(ratio / r_center))
        if si2 - si1 > 1 or ti2 - ti1 > 1:
            cost += _MERGE_PENALTY   # 无惩罚时两对良好1:1的聚合2:2与1:1等价→DP无脑合并

        sw = set()
        for k in range(si1, si2):
            sw |= sword[k]
        if sw:
            tb = set()
            for k in range(ti1, ti2):
                tb |= tword[k]
            hits = 0
            for w in sw:
                if w in tb:
                    hits += 1
            cost -= (hits / len(sw)) * _OVERLAP_BONUS

        # D9.2：语义相似度奖励——只奖励高置信匹配（sim>0.6），
        #       避免中等相似度的错配也被"免费化"导致 DP 选偏移路径
        if sim is not None:
            sem = max(sim[si][ti]
                      for si in range(si1, si2) for ti in range(ti1, ti2))
            if sem > _SEM_FLOOR:
                cost -= _SEM_BONUS * (sem - _SEM_FLOOR)
        return max(0.0, cost)

    # 字典存储：仅保存 |i-j|<=band 的单元（带状）——大文档不整矩阵分配，防 OOM
    INF = float("inf")
    dp: dict = {(0, 0): 0.0}
    back: dict = {}

    for i in range(m + 1):
        lo = max(0, i - band)
        hi = min(n, i + band)
        for j in range(lo, hi + 1):
            if i == 0 and j == 0:
                continue
            best, best_blk = INF, None

            pc = dp.get((i - 1, j - 1))
            if pc is not None:
                c = pc + block_cost(i - 1, i, j - 1, j)
                if c < best:
                    best, best_blk = c, (((i - 1,), (j - 1,)), i - 1, j - 1)
            if j >= 2:
                pc = dp.get((i - 1, j - 2))
                if pc is not None:
                    c = pc + block_cost(i - 1, i, j - 2, j)
                    if c < best:
                        best, best_blk = c, (((i - 1,), (j - 2, j - 1)), i - 1, j - 2)
            if i >= 2:
                pc = dp.get((i - 2, j - 1))
                if pc is not None:
                    c = pc + block_cost(i - 2, i, j - 1, j)
                    if c < best:
                        best, best_blk = c, (((i - 2, i - 1), (j - 1,)), i - 2, j - 1)
            if i >= 2 and j >= 2:
                pc = dp.get((i - 2, j - 2))
                if pc is not None:
                    c = pc + block_cost(i - 2, i, j - 2, j)
                    if c < best:
                        best, best_blk = c, (((i - 2, i - 1), (j - 2, j - 1)), i - 2, j - 2)
            pc = dp.get((i - 1, j))
            if pc is not None:
                c = pc + _SKIP_COST
                if c < best:
                    best, best_blk = c, (((i - 1,), ()), i - 1, j)
            if j >= 1:
                pc = dp.get((i, j - 1))
                if pc is not None:
                    c = pc + _SKIP_COST
                    if c < best:
                        best, best_blk = c, (((), (j - 1,)), i, j - 1)

            if best_blk is not None:
                dp[(i, j)] = best
                back[(i, j)] = best_blk

    # 回溯
    blocks: list[tuple] = []
    i, j = m, n
    while i > 0 or j > 0:
        entry = back.get((i, j))
        if entry is None:
            # 理论上不可达（带状死路）——用跳过补全，避免死循环
            if i > 0:
                blocks.append(((i - 1,), ()))
                i -= 1
            elif j > 0:
                blocks.append(((), (j - 1,)))
                j -= 1
        else:
            blk, pi, pj = entry
            blocks.append(blk)
            i, j = pi, pj
    blocks.reverse()
    return blocks


def _is_heading(text: str) -> bool:
    """MD标题行（# 开头）。"""
    return bool(text) and text.lstrip().startswith("#")


# 句读标点切分（分支1：句点/分号后跟空白·拉丁语系+空格分隔；分支2：中文句号/叹号/问号后直接跟非空白）
_SENT_SPLIT_RE = re.compile(r'(?<=[.;!?。；！？])\s+|(?<=[。！？])(?=[^\s])')
# 列表项开头（- * + • 或 "1." "1)" "1、"）
_LIST_MARKER_RE = re.compile(r'^(?:[-*+•]|\d+[.)、])\s')
# D9.2：元数据/标签行（期刊头部·无句读标点）→ 独立句，避免折叠进正文成超长"句"
_META_LINE_RE = re.compile(
    r'^(?:Received|Accepted|Published|Submitted|Revised|Corresponding|'
    r'Open\s+Access|Review\s+Article|Abstract|ABSTRACT|Keywords|KEYWORDS|'
    r'Email|E-mail|ISSN|DOI|ORCID)\b'
    r'|^[A-Za-z][A-Za-z0-9&\'()/.\- ]{0,20}:'
)
# D9.2：期刊元数据噪音行（无稳定长度比·不参与对齐）·源（英）+ 译（中）两侧
# 注：ABSTRACT/摘要 仅独立标签才过滤（与正文同行的"ABSTRACT 云计算…"是内容·保留）
_META_NOISE_RE = re.compile(
    r'^(?:Email|E-mail|Received|Accepted|Published|Submitted|Revised|ISSN|DOI|ORCID|'
    r'Corresponding author|Open Access|Review Article)\b'
    r'|^(?:ABSTRACT|摘要)\s*$'
    r'|^(?:电子邮件|电子邮箱|邮箱|收稿日期|录用日期|接收日期|出版日期|通讯作者|开放获取|综述文章)'
)
# 拉丁词/数字（内容重叠奖励用）
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]*|\d+")


def _split_sentences(text: str) -> list[str]:
    """中英文混合句级分割。

    D7修复（多行折叠英文句被碎片化）：原先把段内每个换行都当句子边界，
    源文多行折叠句被切成碎片（源21"句"vs译6句）→ 顺序对齐错位 → 质检按
    错位句对误判漏译。现改为：
      1. 先按MD标题(#)分块 → 标题独立成句
      2. 块内按句读标点切段（段以句号/问号/叹号/分号结尾·中英文句号后无空格均支持）
      3. 段内按空行分段落（空行=独立语块·保留无#标题/列表项边界）
      4. 段落内折叠续行：非列表项开头的行是上一句的续行 → 拼回当前句
    """
    blocks = re.split(r'(\n#{1,6}\s+.+\n)', text)

    sentences: list[str] = []
    for block in blocks:
        if block.startswith('\n#'):
            sentences.append(block.strip())
            continue

        segs = _SENT_SPLIT_RE.split(block)
        for seg in segs:
            paragraphs = [p for p in seg.split('\n\n') if p.strip()]
            for para in paragraphs:
                buffer = ""
                for line in (l.strip() for l in para.split('\n') if l.strip()):
                    # D9.2：元数据/标签行起点 → 结束当前句、另起新句（否则折叠成超长句导致对齐漂移）
                    if buffer and (_LIST_MARKER_RE.match(line) or _META_LINE_RE.match(line)):
                        sentences.append(buffer)
                        buffer = line
                    else:
                        buffer = (buffer + " " + line) if buffer else line
                if buffer:
                    sentences.append(buffer)

    return [s for s in sentences if s and len(s) > 3]


def _alignment_score(src: str, tgt: str) -> float:
    """
    计算对齐置信度（0-1）。

    综合考虑：
      - 长度比（中文字数 vs 英文词数，合理比例 ~0.5-2.0）
      - 关键词重叠率（英文关键词中的字母是否出现在译文中）
    """
    # 长度比
    src_len = len(src)
    tgt_len = len(tgt)
    if src_len == 0 or tgt_len == 0:
        return 0.0

    ratio = min(src_len, tgt_len) / max(src_len, tgt_len)

    # 关键词重叠（简单的字母/中文匹配）
    src_words = set(re.findall(r'[a-zA-Z]+', src.lower()))
    tgt_text = tgt.lower()
    if src_words:
        hits = sum(1 for w in src_words if w.lower() in tgt_text)
        word_overlap = hits / len(src_words)
    else:
        word_overlap = 0.5  # 无法判断时给中性分

    return round(ratio * 0.4 + word_overlap * 0.6, 3)


# ══════════════════════════════════════════════════════════════════
# D6新增：按chunk对齐 + 摘抄句匹配定位
# ══════════════════════════════════════════════════════════════════

def build_triple_alignment(
    source_md: str,
    draft_md: str,
    final_md: str,
) -> list[dict]:
    """
    三栏句对齐：源句 ↔ 初译句 ↔ 终译句 逐行对照。

    源句为主键：两次对齐共用同一源句序列（同为 align_sentences 且同一 source_md
    → 行主键一致），按 source_seg 以队列消费拼行——容忍源句中重复句，
    也容忍两次对齐行数不一致（缺一侧时该列留空）。

    Returns:
        [{"source_seg": 源句, "draft_seg": 初译句, "final_seg": 终译句}, ...]
    """
    pairs_draft = align_sentences(source_md, draft_md)
    # D9.2：终译对"初译"对齐（同语言·语义几乎相同·比跨语言源↔终译可靠）——
    # 源↔终译对齐受润色句子结构变化影响会漂移，初译↔终译则稳定
    pairs_ff = align_sentences(draft_md, final_md)

    final_by_draft: dict[str, deque] = defaultdict(deque)
    for pf in pairs_ff:
        final_by_draft[pf.source_seg].append(pf)

    rows: list[dict] = []
    for pd in pairs_draft:
        q = final_by_draft.get(pd.target_seg)
        pf = q.popleft() if q else None
        rows.append({
            "source_seg": pd.source_seg,
            "draft_seg": pd.target_seg,
            "final_seg": pf.target_seg if pf else "",
        })
    return rows


def align_chunks(chunks: list, chunk_drafts: list) -> list[AlignedPair]:
    """
    按chunk逐块对齐：源chunk ↔ 对应块译文，填充 chunk_id。

    与 align_sentences（全文一次对齐）的区别：
      - 每块的句对自带 chunk_id（质检定位"哪一块"）
      - 块内对齐，避免跨块句序漂移

    Args:
        chunks: list[Chunk]
        chunk_drafts: list[str] 逐chunk译文（与chunks同序）

    Returns:
        list[AlignedPair]：全部块的句对拼接（chunk_id 已填充）
    """
    all_pairs: list[AlignedPair] = []
    for i, chunk in enumerate(chunks):
        draft = chunk_drafts[i] if i < len(chunk_drafts) else ""
        if not draft:
            continue
        block_pairs = align_sentences(chunk.source_text, draft)
        cid = chunk.chunk_id or f"chunk_{i + 1}"
        for p in block_pairs:
            p.chunk_id = cid
        all_pairs.extend(block_pairs)
    return all_pairs


def locate_quote(
    pairs: list[AlignedPair],
    source_quote: str,
    target_quote: str,
    threshold: float = 0.35,
) -> tuple:
    """
    把质检LLM摘抄的源/译句，模糊匹配到具体句对。

    策略：LLM擅长摘抄、不擅长数编号 → 定位以本函数为准。
    对每个句对分别算 源句相似度 + 译句相似度，加权取最高分者。

    Args:
        pairs: pool.aligned_pairs（初译稿对齐结果）
        source_quote: 质检摘抄的源句原文（可空）
        target_quote: 质检摘抄的译句原文（可空）
        threshold: 低于该分视为未匹配

    Returns:
        (AlignedPair|None, index)：匹配到的句对与它在 pairs 中的下标；未匹配为 (None, -1)
    """
    if not pairs:
        return None, -1
    if not source_quote.strip() and not target_quote.strip():
        return None, -1

    best_pair = None
    best_index = -1
    best_score = 0.0

    for idx, pair in enumerate(pairs):
        s_score = _quote_similarity(source_quote, pair.source_seg) if source_quote.strip() else 0.0
        t_score = _quote_similarity(target_quote, pair.target_seg) if target_quote.strip() else 0.0
        if s_score == 0.0 and t_score == 0.0:
            score = 0.0
        elif source_quote.strip() and target_quote.strip():
            # 双侧都给时取min：源/译摘抄必须匹配同一句对。
            # 加权平均会让"源匹配句对A、译匹配句对B"的跨句对错配靠单侧高分过阈值、
            # 被权威钉死到错误句对（如LLM误把 Main Objectives 与 性能与韧性… 当成一句）。
            # min 使跨句对错配两侧都低分 → 低于阈值不钉死，保留LLM原始指认（不精确但不误导）。
            score = min(s_score, t_score)
        elif source_quote.strip():
            score = s_score
        else:
            score = t_score

        if score > best_score:
            best_score = score
            best_pair = pair
            best_index = idx

    if best_score < threshold:
        return None, -1
    return best_pair, best_index


def _quote_similarity(a: str, b: str) -> float:
    """摘抄句 vs 句对句子 的归一化相似度（0-1）。"""
    if not a or not b:
        return 0.0
    na = _normalize_quote(a)
    nb = _normalize_quote(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _normalize_quote(s: str) -> str:
    """归一化：去空白、去常见标点、小写。容忍 LLM 摘抄时的细微出入。"""
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，。；！？,.!?;:'\"'\"()（）\[\]【】《》<>·—…·、]", "", s)
    return s.lower()

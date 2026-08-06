"""
异常降级处理
============
Vibe Coder A | v1.0 | 2026-08-06

职责：LLM调用失败时的分级降级策略（L0→L1→L2→L3）。
      确保产品在最差情况下仍能提供「不可译区域保护」这一核心价值。

故障分级：
  L0·静默恢复 — 自动重试（用户无感知）
  L1·降级可用 — 跳过非致命环节，翻译仍可用
  L2·人工接管 — 暂停等待用户决策
  L3·完整熔断 — 翻译失败，友好的错误提示
"""

from transagent.interface import TranslationSession, StepState, DegradationLevel


async def handle_degradation(
    session: TranslationSession,
    error: Exception,
    progress,
) -> None:
    """
    根据当前降级等级和错误类型，执行对应的降级策略。

    Args:
        session: 当前翻译会话
        error: 触发的异常
        progress: 进度回调函数
    """
    level = session.degradation_level

    if level == DegradationLevel.L3:
        # 完整熔断：翻译无法继续
        _set_all_remaining_failed(session)
        progress("export", StepState.FAILED,
                 f"翻译中止（L3熔断）: {error}\n"
                 f"您的文档已保留预处理结果，可稍后重试。")
        return

    if level == DegradationLevel.L2:
        # 需用户决策：暂停流程
        _set_all_remaining_failed(session)
        progress("export", StepState.FAILED,
                 f"翻译中断（需用户决策）: {error}\n"
                 f"已完成步骤的数据已保留："
                 f"术语表={_has_data(session, 'pre_translate')}, "
                 f"初译稿={_has_data(session, 'translate')}")
        return

    if level == DegradationLevel.L1:
        # 降级可用：跳过非致命环节
        session.steps["learn"] = StepState.SKIPPED
        if session.translate_result and session.translate_result.draft:
            session.post_translate_result.final_text = session.translate_result.draft
        progress("learn", StepState.SKIPPED, f"知识库更新跳过（L1降级）")
        return

    # L0: 静默恢复（已在llm_client内部重试，此处为兜底）
    print(f"[Degradation] L0 处理: {error}")


def _set_all_remaining_failed(session: TranslationSession) -> None:
    """将所有未完成步骤标记为失败"""
    for step_name, state in session.steps.items():
        if state in (StepState.PENDING, StepState.IN_PROGRESS):
            session.steps[step_name] = StepState.FAILED


def _has_data(session: TranslationSession, step: str) -> str:
    """检查某步骤是否有数据"""
    if step == "pre_translate":
        return "有" if session.pre_translate_result else "无"
    if step == "translate":
        return "有" if session.translate_result else "无"
    return "未知"

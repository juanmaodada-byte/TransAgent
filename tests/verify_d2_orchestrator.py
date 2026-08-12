"""D2 验收项⑤：Orchestrator 框架集成测试（使用真实LLM）。"""
import sys, io, asyncio, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.backend.core import Orchestrator
from transagent.interface import StepState

print('=== 验收项⑤：Orchestrator 框架集成测试 ===')
print()

steps_seen = []

async def main():
    o = Orchestrator('test_d2_user')

    def progress(step, state, msg):
        steps_seen.append((step, state.value, msg))
        icon = {
            'completed': 'V', 'in_progress': '>', 'failed': 'X',
            'skipped': 'o', 'waiting_user': '?'
        }.get(state.value, '.')
        print(f'  [{icon}] {step}: {msg}')

    t0 = time.time()
    session = await o.translate(
        'transagent/workspace/test_d2.md',
        on_progress=progress,
    )
    elapsed = time.time() - t0

    print()
    print('=== 结果 ===')
    completed = sum(1 for s in session.steps.values() if s == StepState.COMPLETED)
    total = len(session.steps)
    print(f'  步骤完成: {completed}/{total}')
    print(f'  总耗时: {elapsed:.1f}s')
    print(f'  错误数: {len(session.errors)}')
    if session.errors:
        for e in session.errors:
            print(f'    - {e[:120]}')
    print(f'  降级等级: {session.degradation_level}')
    print(f'  终稿长度: {len(session.final_text_restored)} 字符')

    if session.post_translate_result and session.post_translate_result.qa_report:
        qa = session.post_translate_result.qa_report
        print(f'  质检总分: {qa.total_score}/10')

    if session.evolution_report:
        ev = session.evolution_report
        print(f'  进化: +{ev.new_terms_count}术语 +{ev.new_tm_count}TM '
              f'| 累计术语{ev.total_terms} TM{ev.total_tm}')

    print()
    print('=== 框架集成验证 ===')
    for step, state, msg in steps_seen:
        print(f'    {step}: {state}')

    has_final = len(session.final_text_restored) > 0
    print(f'  Session ID: {session.session_id}')
    print(f'  终稿非空: {has_final}')
    print(f'  框架日志出现: 见上方 [Framework] 行')
    no_critical = len(session.errors) == 0
    print(f'  无错误: {no_critical}')

    all_ok = completed >= 7 and has_final and no_critical
    print()
    if all_ok:
        print('  >>> 验收项⑤ 通过：Orchestrator 框架集成正常工作 <<<')
    else:
        print('  X 验收项⑤ 未通过（检查上方错误信息）')
        if not no_critical:
            print(f'    错误: {session.errors}')


asyncio.run(main())

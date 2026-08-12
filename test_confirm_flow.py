"""
术语确认闭环端到端测试（临时脚本）
====================================
模拟：前端上传 → 启动SSE → 收到 terms_pending → POST confirm_terms → 翻译继续 → done。

用法（从项目根目录运行，确保后端已启动）：
    python transagent/test_confirm_flow.py
"""

import asyncio
import json
import sys

import httpx

BASE = "http://localhost:8000"
TEST_MD = """# Advanced Kubernetes Rollout Patterns

## Blue-Green and Canary Deployments

Kubernetes supports progressive delivery through rollout strategies. A **canary deployment** routes a small percentage of traffic to the new version before full promotion. The **replica set** controller maintains the desired number of pods.

## Stateful Workloads

StatefulSets manage the deployment and scaling of stateful applications. Each pod in a StatefulSet has a stable network identity. PersistentVolumeClaims provide durable storage across pod rescheduling events.

## Admission Webhooks

An admission controller intercepts requests to the API server. A **mutating admission webhook** modifies resources before they are persisted, while a **validating admission webhook** rejects non-compliant configurations.

## Observability

The **metrics server** aggregates resource usage from kubelets. Prometheus scrapes metrics endpoints with a configurable scraping interval. The **HorizontalPodAutoscaler** adjusts replica counts based on observed CPU utilization.
"""


async def main():
    async with httpx.AsyncClient() as client:
        # 1. 上传
        r = await client.post(f"{BASE}/api/upload",
                              files={"file": ("networking.md", TEST_MD.encode(), "text/markdown")})
        assert r.status_code == 200, r.text
        file_id = r.json()["file_id"]
        print(f"[1] 上传成功 file_id={file_id}")

        # 2. 启动 SSE 翻译
        terms_pending_evt = None
        done_evt = None
        strategy_evt = None

        async def consume_sse():
            nonlocal terms_pending_evt, done_evt, strategy_evt
            async with client.stream("POST", f"{BASE}/api/translate",
                                     data={"file_id": file_id, "user_id": "confirm_test"},
                                     timeout=120) as resp:
                assert resp.status_code == 200, await resp.aread()
                event_type = ""
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: ") and event_type:
                        data = json.loads(line[6:])
                        if event_type == "terms_pending":
                            terms_pending_evt = data
                            print(f"[2] 收到 terms_pending：{len(data['pending_terms'])} 个待确认术语")
                            print(f"     session_id={data['session_id']}")
                            print(f"     示例: {json.dumps(data['pending_terms'][0], ensure_ascii=False)}")
                            # 3. 立即确认（应用确认结果）
                            confirmed = [
                                {
                                    "term": t["term"],
                                    "translation": t["translation"] + "✓",
                                    "action": t.get("action", "translate"),
                                    "confidence": "high",
                                    "source": "用户确认",
                                }
                                for t in data["pending_terms"]
                            ]
                            resp2 = await client.post(
                                f"{BASE}/api/confirm_terms",
                                data={"session_id": data["session_id"],
                                      "confirmed_terms": json.dumps(confirmed, ensure_ascii=False)})
                            print(f"[3] confirm_terms 响应: {resp2.json()}")
                        elif event_type == "strategy":
                            strategy_evt = data
                        elif event_type == "done":
                            done_evt = data
                            print(f"[4] done: session_id={data['session_id']} "
                                  f"elapsed={data['elapsed_seconds']:.1f}s")
                        elif event_type == "final":
                            print(f"[5] final_text 长度={len(data['final_text'])}")

        try:
            await asyncio.wait_for(consume_sse(), timeout=90)
        except asyncio.TimeoutError:
            print("!! SSE 超时未完成")

        # 断言
        ok = True
        if terms_pending_evt is None:
            print("✗ 未收到 terms_pending 事件（术语确认断点未触发）")
            ok = False
        if done_evt is None:
            print("✗ 未收到 done 事件（翻译未继续）")
            ok = False
        if strategy_evt is None:
            print("✗ 未收到 strategy 事件")
            ok = False

        print("\n" + ("✅ 术语确认闭环测试通过" if ok else "❌ 测试失败"))
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

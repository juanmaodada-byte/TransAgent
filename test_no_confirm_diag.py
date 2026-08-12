"""
诊断：连接 SSE 收到 terms_pending 后不确认，观察翻译是否自行继续。
如果收到 final/done（未确认就继续）→ 后端未真正暂停。
如果一直停在 terms_pending → 后端已正确暂停。
"""

import asyncio
import json
import sys

import httpx

BASE = "http://localhost:8000"
TEST_MD = """# Kubernetes Networking

## Service Types

Kubernetes provides several Service types. A ClusterIP exposes the Service on an internal IP.

```bash
kubectl expose pod nginx --type=NodePort
```

## Ingress

Ingress manages external access. It can provide load balancing, SSL termination, and name-based virtual hosting.
"""


async def main():
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/upload",
                              files={"file": ("diag.md", TEST_MD.encode(), "text/markdown")})
        file_id = r.json()["file_id"]
        print(f"file_id={file_id}")

        events: list[str] = []
        got_pending = asyncio.Event()
        monitor = asyncio.Event()

        async def consume():
            async with client.stream("POST", f"{BASE}/api/translate",
                                     data={"file_id": file_id, "user_id": "diag_user"},
                                     timeout=60) as resp:
                event_type = ""
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        events.append(event_type)
                        if event_type == "terms_pending":
                            print(f"[t=0] 收到 terms_pending，开始等待 8 秒（不确认）…")
                            got_pending.set()
                        if event_type in ("final", "done"):
                            print(f"[!!] 未确认就收到 {event_type} —— 翻译未暂停！")
                        monitor.set()

        task = asyncio.create_task(consume())

        try:
            await asyncio.wait_for(got_pending.wait(), timeout=30)
        except asyncio.TimeoutError:
            print("!! 30 秒内未收到 terms_pending（可能没有待确认术语）")
            await asyncio.wait_for(monitor.wait(), timeout=20)
            await task
            print(f"事件序列: {events}")
            return

        # 收到 terms_pending 后等待 8 秒，期间不确认
        print("等待 8 秒…")
        try:
            await asyncio.wait_for(monitor.wait(), timeout=8)
            print(f"[结果] 8 秒内收到后续事件: {[e for e in events if e not in ('progress',)]}")
        except asyncio.TimeoutError:
            print("[结果] 8 秒内无后续事件 —— 翻译已正确暂停等待确认 ✅")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""
一句话翻译诊断：记录每个 SSE 事件的时间戳，定位卡顿与术语确认行为。
"""

import asyncio
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"
SENTENCE = "Kubernetes is a container orchestration platform that automates deployment."  # 一句话


async def main():
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/upload",
                              files={"file": ("sentence.md", SENTENCE.encode(), "text/markdown")})
        file_id = r.json()["file_id"]
        print(f"[{time.time():.0f}] 上传成功 file_id={file_id}")

        t_start = time.time()
        events: list[tuple[float, str, str]] = []

        async with client.stream("POST", f"{BASE}/api/translate",
                                 data={"file_id": file_id, "user_id": "diag_user"},
                                 timeout=400) as resp:
            event_type = ""
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    t = time.time() - t_start
                    data = json.loads(line[6:])
                    if event_type == "progress":
                        events.append((t, "progress", f"{data['step']}:{data['state']} | {data['message'][:40]}"))
                    elif event_type in ("strategy", "terms", "terms_pending", "final", "qa", "evolution", "done", "error"):
                        events.append((t, event_type, line[6:][:120]))

        # 输出时间线
        print("\n=== 事件时间线 ===")
        for t, et, desc in events:
            print(f"[{t:7.1f}s] {et:14s} {desc}")
        print(f"\n总耗时: {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

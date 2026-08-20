"""验证三栏句对齐：上传文档 → SSE → 检查 final 事件的 aligned_rows。"""

import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
DOC = """# Kubernetes Networking

## Service Types

Kubernetes provides several Service types for exposing applications. A ClusterIP exposes the Service on an internal IP.

## Ingress

Ingress manages external access. It can provide load balancing and SSL termination.
"""


async def main():
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/upload",
                              files={"file": ("net.md", DOC.encode(), "text/markdown")})
        assert r.status_code == 200, r.text
        file_id = r.json()["file_id"]
        print(f"file_id={file_id}")

        async with client.stream("POST", f"{BASE}/api/translate",
                                 data={"file_id": file_id, "user_id": "triple_test"},
                                 timeout=400) as resp:
            event_type = ""
            final_data = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: ") and event_type == "final":
                    final_data = json.loads(line[6:])

        if not final_data:
            print("!! 未收到 final 事件")
            return 1

        rows = final_data.get("aligned_rows", [])
        print(f"\naligned_rows 数量: {len(rows)}")
        for r in rows[:6]:
            print(f"  [{r['source_seg'][:50]}]")
            print(f"    初译: {r['draft_seg'][:50]}")
            print(f"    终译: {r['final_seg'][:50]}")
        ok = len(rows) > 0
        print("\n" + ("✅ 三栏句对齐正常" if ok else "❌ 无对齐数据"))
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""一次性导入种子术语到 RAG(批量预嵌入,落盘持久化)。

用法:
    python -m knowledge_base.import_seed           # 幂等:已有词条按 id 覆盖
    python -m knowledge_base.import_seed --force   # 清空 collection 后重建

首次运行会下载 bge-m3 模型(~2.2GB,墙内走 hf-mirror),之后加载本地缓存。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import TermEntry          # noqa: E402
from knowledge_base import config        # noqa: E402
from knowledge_base.rag_terms import reset_collection, _collection, write_rag  # noqa: E402


def load_seed(path: Path = config.SEED_JSON_PATH) -> list[TermEntry]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    entries = []
    for item in raw:
        entries.append(TermEntry(
            term=item["term"],
            translation=item["translation"],
            domain=item.get("domain", ""),
            confidence=item.get("confidence", "high"),
            action=item.get("action", "translate"),
            source=item.get("source", "种子数据"),
        ))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="导入种子术语到 RAG 术语库")
    parser.add_argument("--force", action="store_true", help="清空 collection 后重建")
    args = parser.parse_args()

    col = _collection()
    if args.force:
        n = reset_collection(config.DEFAULT_USER)
        print(f"[import_seed] 已清空 collection + 别名表,删除 {n} 条")

    entries = load_seed()
    if not entries:
        print("[import_seed] seed_terms.json 为空,退出")
        return

    t0 = time.time()
    ids = write_rag(entries, user_id=config.DEFAULT_USER)
    print(f"[import_seed] 完成:写入 {len(ids)} 条(去重后 collection 现有 {col.count()} 条)"
          f",耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()

"""知识库模块冒烟测试(SQLite 部分,不依赖 bge-m3,秒级完成)。

用法:
    python tests/test_smoke.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import TMEntry                    # noqa: E402
from knowledge_base import (                     # noqa: E402
    init_collection, init_tm_db, init_prefs_db,
    load_prefs, save_prefs, search_tm, write_tm,
)

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def test_tm() -> None:
    print("TM 翻译记忆:")
    uid = "smoke_user"

    init_tm_db()
    write_tm(TMEntry(source_seg="Run the following command:",
                     target_seg="运行以下命令：", quality_score=9.2), user_id=uid)

    # 精确命中
    hits = search_tm("Run the following command:", user_id=uid)
    check("精确句命中", len(hits) == 1 and hits[0].similarity >= 0.85,
          f"got {len(hits)}")
    check("返回正确译文", hits and hits[0].target_seg == "运行以下命令：")

    # 92.9% 相似句命中(加一个词)
    hits2 = search_tm("Run the following command now:", user_id=uid)
    check("近似句命中(sim>=0.85)", hits2 and hits2[0].similarity >= 0.85,
          f"sim={hits2[0].similarity if hits2 else 0:.2f}")

    # 语义改写(~79%)应按设计不命中——TM 是字符串匹配,语义改写由 RAG 术语库兜底
    hits3 = search_tm("Run the command as follows:", user_id=uid)
    check("语义改写不命中(设计行为)", len(hits3) == 0, f"got {len(hits3)}")

    # 无关句应未命中
    hits3 = search_tm("The weather is nice today", user_id=uid)
    check("无关句未命中", len(hits3) == 0, f"got {len(hits3)}")

    # 幂等:重复写入不堆积
    write_tm(TMEntry(source_seg="Run the following command:",
                     target_seg="运行以下命令：", quality_score=9.5), user_id=uid)
    hits4 = search_tm("Run the following command:", top_k=10, user_id=uid)
    check("重复写入不堆积", sum(1 for h in hits4 if h.similarity == 1.0) == 1)


def test_prefs() -> None:
    print("用户偏好:")
    uid = "smoke_user"
    init_prefs_db()
    save_prefs(uid, {"style": "技术文档", "direct_or_free": "直译为主", "tone": "专业"})
    prefs = load_prefs(uid)
    check("偏好往返一致", prefs.get("style") == "技术文档"
          and prefs.get("direct_or_free") == "直译为主"
          and prefs.get("tone") == "专业", f"got {prefs}")
    save_prefs(uid, {"style": "简洁"})   # 覆盖一个 key
    prefs2 = load_prefs(uid)
    check("覆盖更新", prefs2.get("style") == "简洁" and prefs2.get("tone") == "专业",
          f"got {prefs2}")
    check("空用户返回空 dict", load_prefs("nobody") == {})


def test_collection() -> None:
    print("RAG collection 初始化:")
    n = init_collection()
    check("collection 存在(未导入种子前应为 0)", isinstance(n, int) and n >= 0, f"n={n}")


if __name__ == "__main__":
    test_collection()
    test_tm()
    test_prefs()
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)

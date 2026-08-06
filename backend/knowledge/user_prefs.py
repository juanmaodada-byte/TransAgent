"""
用户偏好管理
============
成员 C | v1.0 | 2026-08-06

职责：用户翻译偏好Profile的读写。
      目前用JSON文件存储（MVP阶段·后续可迁移到SQLite）。

使用：
    from transagent.backend.knowledge.user_prefs import load_user_prefs, save_user_prefs
    prefs = load_user_prefs("user_001")
"""

import json
import os
from transagent.interface import UserPrefs
from transagent.backend.config import get_config


_PREFS_DIR = "./data/prefs"


def load_user_prefs(user_id: str) -> UserPrefs:
    """加载用户偏好Profile。不存在时返回默认值。"""
    os.makedirs(_PREFS_DIR, exist_ok=True)
    prefs_file = os.path.join(_PREFS_DIR, f"{user_id}.json")

    if not os.path.exists(prefs_file):
        return UserPrefs(user_id=user_id)

    try:
        with open(prefs_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return UserPrefs(
            user_id=data.get("user_id", user_id),
            default_style=data.get("default_style", "technical"),
            domain_tags=data.get("domain_tags", []),
            term_preferences=data.get("term_preferences", {}),
            strategy_history=data.get("strategy_history", []),
            literal_ratio=data.get("literal_ratio", 0.6),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
    except Exception as e:
        print(f"[Prefs] 加载失败，使用默认: {e}")
        return UserPrefs(user_id=user_id)


def save_user_prefs(prefs: UserPrefs) -> None:
    """保存用户偏好Profile。"""
    import datetime

    os.makedirs(_PREFS_DIR, exist_ok=True)
    prefs_file = os.path.join(_PREFS_DIR, f"{prefs.user_id}.json")

    now = datetime.datetime.now().isoformat()
    if not prefs.created_at:
        prefs.created_at = now
    prefs.updated_at = now

    try:
        with open(prefs_file, "w", encoding="utf-8") as f:
            json.dump(prefs.to_dict(), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Prefs] 保存失败: {e}")

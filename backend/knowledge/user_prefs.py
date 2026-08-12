"""
用户偏好 —— 薄适配器(方案A·整合 2026-08-11)
=================================================
对外保持旧骨架签名(A 端 orchestrator 零改动),内部委托成员 C 新交付包
(rag/rag/knowledge_base,见 _backend.py)。契约转回 transagent.interface.UserPrefs。

保留接口(旧签名):
    load_user_prefs(user_id) -> UserPrefs
    save_user_prefs(prefs) -> None

集成要点:
  - 存储形态:旧骨架为 JSON 文件(./data/prefs/*.json),新包为 SQLite(tm.db 内
    user_prefs 表)。偏好按 user_id 原样存取(不映射,偏好属用户身份而非共享积累)。
  - 返回值:新包 load_prefs 返回 {preference_type: value} dict,适配层映射回
    UserPrefs 常用字段;其余字段保持默认。
"""
import json

from transagent.interface import UserPrefs
from ._backend import user_prefs as _prefs


def load_user_prefs(user_id: str) -> UserPrefs:
    """旧签名。新包 load_prefs → UserPrefs(不存在的用户返回默认偏好)。

    新包 SQLite 按 preference_type:value 字符串存储;dict/list 类型经 JSON 编码,
    读取时反解,保证与 interface.UserPrefs 字段类型一致。
    """
    raw = _prefs.load_prefs(user_id)
    return UserPrefs(
        user_id=user_id,
        default_style=str(raw.get("default_style", "technical")),
        literal_ratio=float(raw.get("literal_ratio", 0.6) or 0.6),
        term_preferences=_load_json(raw.get("term_preferences"), {}),
        domain_tags=_load_json(raw.get("domain_tags"), []),
    )


def save_user_prefs(prefs: UserPrefs) -> None:
    """旧签名。将 UserPrefs 常用字段落库(SQLite upsert,失败不影响主流程)。"""
    try:
        d = prefs.to_dict()
        _prefs.save_prefs(prefs.user_id, {
            "default_style": d.get("default_style", "technical"),
            "literal_ratio": d.get("literal_ratio", 0.6),
            "term_preferences": json.dumps(d.get("term_preferences", {}), ensure_ascii=False),
            "domain_tags": json.dumps(d.get("domain_tags", []), ensure_ascii=False),
        })
    except Exception as e:
        print(f"[Prefs] 保存失败: {e}")


def _load_json(value, fallback):
    """偏好值字符串 → 原类型;非 JSON(或空)回退默认。"""
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback

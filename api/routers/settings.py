"""調整可能パラメータの永続上書き（Web編集 → 実行時マージ）API"""

from fastapi import APIRouter, Depends, HTTPException

import config
from api.deps import get_db
from api.schemas import SettingItem, SettingsUpdateIn
from data.repository import get_param_overrides, save_param_overrides

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _default_of(param: str):
    """上書きを除いたデフォルト値（config の生 dict 由来）。"""
    section = getattr(config, config.PARAM_SECTIONS[param])
    return section.get(param)


def _coerce(param: str, value) -> float | int | bool:
    """入力値をパラメータのデフォルト型に合わせる（int 値が float 化して窓幅等を壊さないように）。"""
    default = _default_of(param)
    if isinstance(default, bool):       # bool は int より先に判定（True は int でもある）
        return bool(value)
    if isinstance(default, int):
        return int(value)
    return float(value)


def _build_items(conn) -> list[dict]:
    overrides = get_param_overrides(conn)
    items = []
    for param, section in config.PARAM_SECTIONS.items():
        default = _default_of(param)
        if default is None:
            continue
        overridden = param in overrides
        items.append({
            "param": param,
            "section": section,
            "value": overrides[param] if overridden else default,
            "default": default,
            "overridden": overridden,
        })
    return items


@router.get("", response_model=list[SettingItem])
def get_settings(conn=Depends(get_db)):
    """調整可能パラメータの現在状態（有効値・デフォルト・上書き有無）を返す。"""
    return _build_items(conn)


@router.put("", response_model=list[SettingItem])
def update_settings(body: SettingsUpdateIn, conn=Depends(get_db)):
    """パラメータ上書きを部分更新で保存する（未知キーは 400）。"""
    unknown = [k for k in body.values if k not in config.PARAM_SECTIONS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知のパラメータ: {', '.join(unknown)}")
    overrides = get_param_overrides(conn)
    for param, value in body.values.items():
        overrides[param] = _coerce(param, value)
    save_param_overrides(conn, overrides)
    return _build_items(conn)


@router.delete("/{param}", response_model=list[SettingItem])
def reset_setting(param: str, conn=Depends(get_db)):
    """指定パラメータの上書きを削除してデフォルトに戻す。"""
    if param not in config.PARAM_SECTIONS:
        raise HTTPException(status_code=404, detail=f"未知のパラメータ: {param}")
    overrides = get_param_overrides(conn)
    if param in overrides:
        del overrides[param]
        save_param_overrides(conn, overrides)
    return _build_items(conn)

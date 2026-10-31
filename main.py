#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RADAR Macro для Python 3.11.9 (локальная версия)
"""

import copy
import ctypes
import json
import math
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process

try:
    import pydirectinput
    pydirectinput.PAUSE = 0
    pydirectinput.FAILSAFE = False
except Exception:
    pydirectinput = None

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QMainWindow


__version__ = "beta-3.7 (max)"

RADAR_WINDOW_TITLE = "RADAR_VIEW"


def enable_dpi_awareness() -> None:
    """Физические пиксели до создания окон и замеров экрана."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


enable_dpi_awareness()


CONFIG_DIR = Path.home() / ".radar_macro"
CONFIG_FILE = CONFIG_DIR / "config.json"

CONFIG_VERSION = 4

DEFAULT_SETTINGS: Dict[str, Any] = {
    "CONFIG_VERSION": CONFIG_VERSION,
    "SQUARE_SIZE": 400,
    "SCAN_INTERVAL": 0.05,
    "SHOW_RADAR_VIEW": True,
    "LOCK_SIZE_MODE": "normal",
    "INTERFACE_MODE": "new",
    "CENTER_USE_KEYS": True,
    "CENTER_KEY_LEFT": "A",
    "CENTER_KEY_RIGHT": "D",
    "CENTER_KEY_UP": "W",
    "CENTER_KEY_DOWN": "S",
    "CENTER_HOLD_SECONDS": 1.0,
    "CENTER_REST_SECONDS": 0.15,
    "CENTER_AUTO_RMB": False,
    "MOTION_MAP_GAIN": 8,
    "ACTIVE_PROFILE": "precision",
    "SEARCH_PROFILES": {
        "precision": {
            "title": "ТОЧНОСТЬ",
            "threshold": 7,
            "dilate": 15,
            "blur": 0,
            "open": 0,
            "min_seed": 0,
            "min_area": 0,
            "max_area": 10000,
            "min_fill": 0.0,
            "max_aspect": 0.0,
            "max_motion_ratio": 1.0,
            "adaptive": False,
            "min_hits": 1,
            "max_misses": 0,
            "match_dist": 0,
        },
        "noise": {
            "title": "ЗАЩИТА ОТ ПОМЕХ",
            "threshold": 20,
            "dilate": 21,
            "blur": 3,
            "open": 3,
            "min_seed": 30,
            "min_area": 120,
            "max_area": 8000,
            "min_fill": 0.2,
            "max_aspect": 4.0,
            "max_motion_ratio": 0.85,
            "adaptive": True,
            "min_hits": 3,
            "max_misses": 1,
            "match_dist": 48,
        },
    },
    "LOCK_SETTINGS": {
        "template_size": 48,
        "far_size": 32,
        "near_size": 64,
        "search_margin": 40,
        "wide_margin": 120,
        "match_min": 0.45,
        "wide_match_min": 0.55,
        "max_misses": 20,
        "lost_margin": 260,
        "lost_timeout": 5.0,
        "lost_match_min": 0.65,
        "update_score": 0.78,
        "template_bank_size": 4,
        "bank_add_score": 0.72,
        "bank_diff_min": 10.0,
    },
    "THERMAL_SETTINGS": {
        "threshold": 7,
        "dot_size": 1,
        "sample_step": 1,
        "bg_alpha": 140,
        "max_dots": 20000,
    },
    "THERMAL_MODE": "window",
    "BINDS": {
        "key_scan": "F",
        "key_thermal": "Q",
        "key_fullscan": "H",
        "key_thermal_mode": "Y",
        "key_tracking": "V",
        "key_profile": "G",
        "key_zoom": "Z",
        "key_zoom_hold": "RMB",
        "key_capture": "T",
        "key_lock_mode": "X",
        "key_center_hold": "N",
        "key_exit": "ESCAPE",
        "key_hide": "Ё",
    },
    "THERMAL_WINDOWS": ["Roblox", "Roblox Player", "ROBLOX"],
}

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Рекурсивно накладывает пользовательский конфиг на дефолты.

    Новые ключи из DEFAULT_SETTINGS появляются автоматически, отсутствующие
    в конфиге ключи больше не приводят к KeyError при импорте.
    """
    result: Dict[str, Any] = {}
    for key, value in base.items():
        incoming = override.get(key, _MISSING)
        if incoming is _MISSING:
            result[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(incoming, dict):
            result[key] = _deep_merge(value, incoming)
        else:
            result[key] = copy.deepcopy(incoming)
    for key, value in override.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
    return result


_MISSING = object()


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return max(low, min(high, number))


def _as_odd(value: Any, default: int, low: int, high: int) -> int:
    number = _as_int(value, default, low, high)
    if number > 0 and number % 2 == 0:
        number += 1
    return number


def _validate_profiles(cfg: Dict[str, Any]) -> None:
    profiles = cfg.get("SEARCH_PROFILES")
    if not isinstance(profiles, dict) or not profiles:
        profiles = copy.deepcopy(DEFAULT_SETTINGS["SEARCH_PROFILES"])
        cfg["SEARCH_PROFILES"] = profiles

    for name in list(profiles):
        base = DEFAULT_SETTINGS["SEARCH_PROFILES"].get(
            name, DEFAULT_SETTINGS["SEARCH_PROFILES"]["precision"]
        )
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            profile = copy.deepcopy(base)
            profiles[name] = profile

        profile["title"] = str(profile.get("title", base["title"]))
        profile["threshold"] = _as_int(profile.get("threshold"), base["threshold"], 1, 255)
        profile["dilate"] = _as_odd(profile.get("dilate"), base["dilate"], 1, 51)
        profile["blur"] = _as_odd(profile.get("blur"), base["blur"], 0, 21)
        profile["open"] = _as_odd(profile.get("open"), base["open"], 0, 15)
        profile["min_seed"] = _as_int(profile.get("min_seed"), base["min_seed"], 0, 100000)
        profile["min_fill"] = round(_as_float(profile.get("min_fill"), base["min_fill"], 0.0, 1.0), 2)
        profile["max_aspect"] = round(
            _as_float(profile.get("max_aspect"), base["max_aspect"], 0.0, 20.0), 2
        )
        profile["max_motion_ratio"] = round(
            _as_float(profile.get("max_motion_ratio"), base["max_motion_ratio"], 0.0, 1.0), 2
        )
        profile["adaptive"] = bool(profile.get("adaptive", base["adaptive"]))
        profile["min_area"] = _as_int(profile.get("min_area"), base["min_area"], 0, 100000)
        profile["max_area"] = _as_int(profile.get("max_area"), base["max_area"], 1, 1000000)
        if profile["max_area"] <= profile["min_area"]:
            profile["max_area"] = profile["min_area"] + 100
        profile["min_hits"] = _as_int(profile.get("min_hits"), base["min_hits"], 1, 15)
        profile["max_misses"] = _as_int(profile.get("max_misses"), base["max_misses"], 0, 10)
        profile["match_dist"] = _as_int(profile.get("match_dist"), base["match_dist"], 0, 1000)


def _validate_binds(cfg: Dict[str, Any]) -> None:
    binds = cfg.get("BINDS")
    if not isinstance(binds, dict):
        binds = copy.deepcopy(DEFAULT_SETTINGS["BINDS"])
        cfg["BINDS"] = binds
    for key, default in DEFAULT_SETTINGS["BINDS"].items():
        value = binds.get(key, default)
        try:
            resolve_vk(str(value))
        except ValueError:
            print(f"[config] неверный бинд {key}={value!r}, беру {default!r}")
            value = default
        binds[key] = str(value)


def _validate_config(cfg: Dict[str, Any]) -> None:
    cfg["CONFIG_VERSION"] = CONFIG_VERSION
    cfg["SQUARE_SIZE"] = _as_int(cfg.get("SQUARE_SIZE"), 400, 64, 2000)
    cfg["SCAN_INTERVAL"] = _as_float(cfg.get("SCAN_INTERVAL"), 0.05, 0.005, 0.5)
    cfg["SHOW_RADAR_VIEW"] = bool(cfg.get("SHOW_RADAR_VIEW", True))
    lock_mode = str(cfg.get("LOCK_SIZE_MODE", "normal")).strip().lower()
    if lock_mode not in ("normal", "far", "near"):
        lock_mode = "normal"
    cfg["LOCK_SIZE_MODE"] = lock_mode
    interface_mode = str(cfg.get("INTERFACE_MODE", "new")).strip().lower()
    if interface_mode not in ("new", "normal"):
        interface_mode = "new"
    cfg["INTERFACE_MODE"] = interface_mode
    cfg["CENTER_USE_KEYS"] = bool(cfg.get("CENTER_USE_KEYS", True))
    for key in (
        "CENTER_KEY_LEFT", "CENTER_KEY_RIGHT",
        "CENTER_KEY_UP", "CENTER_KEY_DOWN",
    ):
        value = cfg.get(key, DEFAULT_SETTINGS[key])
        try:
            resolve_vk(str(value))
        except ValueError:
            print(f"[config] неверная клавиша {key}={value!r}, беру "
                  f"{DEFAULT_SETTINGS[key]!r}")
            value = DEFAULT_SETTINGS[key]
        cfg[key] = str(value)
    cfg["CENTER_HOLD_SECONDS"] = round(
        _as_float(cfg.get("CENTER_HOLD_SECONDS"), 1.0, 0.1, 1.0), 2
    )
    cfg["CENTER_REST_SECONDS"] = round(
        _as_float(cfg.get("CENTER_REST_SECONDS"), 0.15, 0.0, 2.0), 2
    )
    cfg["CENTER_AUTO_RMB"] = bool(cfg.get("CENTER_AUTO_RMB", False))
    cfg["MOTION_MAP_GAIN"] = _as_int(cfg.get("MOTION_MAP_GAIN"), 8, 1, 32)

    lock = cfg.get("LOCK_SETTINGS")
    if not isinstance(lock, dict):
        lock = copy.deepcopy(DEFAULT_SETTINGS["LOCK_SETTINGS"])
        cfg["LOCK_SETTINGS"] = lock
    lock["template_size"] = _as_int(lock.get("template_size"), 48, 16, 160)
    lock["far_size"] = _as_int(lock.get("far_size"), 32, 16, 160)
    lock["near_size"] = _as_int(lock.get("near_size"), 64, 16, 160)
    if lock["far_size"] > lock["template_size"]:
        lock["far_size"] = lock["template_size"]
    if lock["near_size"] < lock["template_size"]:
        lock["near_size"] = lock["template_size"]
    lock["search_margin"] = _as_int(lock.get("search_margin"), 40, 8, 300)
    lock["wide_margin"] = _as_int(lock.get("wide_margin"), 120, 8, 500)
    if lock["wide_margin"] < lock["search_margin"]:
        lock["wide_margin"] = lock["search_margin"]
    lock["match_min"] = round(_as_float(lock.get("match_min"), 0.45, 0.05, 0.95), 2)
    lock["wide_match_min"] = round(
        _as_float(lock.get("wide_match_min"), 0.55, 0.05, 0.99), 2
    )
    if lock["wide_match_min"] < lock["match_min"]:
        lock["wide_match_min"] = lock["match_min"]
    lock["max_misses"] = _as_int(lock.get("max_misses"), 20, 0, 120)
    lock["lost_margin"] = _as_int(lock.get("lost_margin"), 260, 8, 900)
    if lock["lost_margin"] < lock["wide_margin"]:
        lock["lost_margin"] = lock["wide_margin"]
    lock["lost_timeout"] = round(
        _as_float(lock.get("lost_timeout"), 5.0, 0.0, 15.0), 2
    )
    lock["lost_match_min"] = round(
        _as_float(lock.get("lost_match_min"), 0.65, 0.05, 0.99), 2
    )
    if lock["lost_match_min"] < lock["wide_match_min"]:
        lock["lost_match_min"] = lock["wide_match_min"]
    lock["update_score"] = round(_as_float(lock.get("update_score"), 0.78, 0.5, 0.99), 2)
    lock["template_bank_size"] = _as_int(
        lock.get("template_bank_size"), 4, 0, 8
    )
    lock["bank_add_score"] = round(
        _as_float(lock.get("bank_add_score"), 0.72, 0.0, 0.99), 2
    )
    lock["bank_diff_min"] = round(
        _as_float(lock.get("bank_diff_min"), 10.0, 0.0, 100.0), 2
    )

    _validate_profiles(cfg)
    profiles = cfg["SEARCH_PROFILES"]
    if cfg.get("ACTIVE_PROFILE") not in profiles:
        cfg["ACTIVE_PROFILE"] = next(iter(profiles))

    thermal = cfg.get("THERMAL_SETTINGS")
    if not isinstance(thermal, dict):
        thermal = copy.deepcopy(DEFAULT_SETTINGS["THERMAL_SETTINGS"])
        cfg["THERMAL_SETTINGS"] = thermal
    thermal["threshold"] = _as_int(thermal.get("threshold"), 7, 1, 80)
    thermal["dot_size"] = _as_int(thermal.get("dot_size"), 1, 1, 8)
    thermal["sample_step"] = _as_int(thermal.get("sample_step"), 1, 1, 12)
    thermal["bg_alpha"] = _as_int(thermal.get("bg_alpha"), 140, 0, 255)
    thermal["max_dots"] = _as_int(thermal.get("max_dots"), 20000, 1000, 50000)

    thermal_mode_value = str(cfg.get("THERMAL_MODE", "window")).strip().lower()
    if thermal_mode_value not in ("window", "overlay"):
        thermal_mode_value = "window"
    cfg["THERMAL_MODE"] = thermal_mode_value

    windows = cfg.get("THERMAL_WINDOWS")
    if not isinstance(windows, list) or not windows:
        cfg["THERMAL_WINDOWS"] = copy.deepcopy(DEFAULT_SETTINGS["THERMAL_WINDOWS"])

    _validate_binds(cfg)

    for legacy in ("THRESHOLD_VAL", "MIN_TARGET", "MAX_MAP_AREA"):
        cfg.pop(legacy, None)


def load_config() -> Dict[str, Any]:
    """Читает конфиг, мигрирует старый формат и чинит битые значения."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    raw: Dict[str, Any] = {}

    default_path = (
        Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    ) / "default_config.json"

    source = CONFIG_FILE if CONFIG_FILE.exists() else (default_path if default_path.exists() else None)
    if source is not None:
        try:
            with open(source, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("корень конфига должен быть объектом")
            raw = loaded
        except (OSError, ValueError) as exc:
            print(f"[config] не удалось прочитать {source}: {exc}")
            if source == CONFIG_FILE:
                try:
                    shutil.copy2(CONFIG_FILE, CONFIG_FILE.with_name(CONFIG_FILE.name + ".bad"))
                except OSError:
                    pass

    try:
        raw_version = int(raw.get("CONFIG_VERSION", 0))
    except (TypeError, ValueError):
        raw_version = 0
    if raw_version < 3:
        profiles = raw.get("SEARCH_PROFILES")
        if isinstance(profiles, dict) and "noise" in profiles:
            profiles["noise"] = copy.deepcopy(DEFAULT_SETTINGS["SEARCH_PROFILES"]["noise"])
    if raw_version < 4:
        lock = raw.get("LOCK_SETTINGS")
        if isinstance(lock, dict):
            for key, old, new in (
                ("wide_match_min", 0.60, 0.55),
                ("max_misses", 8, 20),
                ("lost_margin", 220, 260),
                ("lost_timeout", 2.0, 5.0),
                ("lost_match_min", 0.70, 0.65),
                ("update_score", 0.85, 0.78),
            ):
                if lock.get(key) == old:
                    lock[key] = new
    if raw_version < CONFIG_VERSION:
        raw["CONFIG_VERSION"] = CONFIG_VERSION

    if "SEARCH_PROFILES" not in raw:
        precision = copy.deepcopy(DEFAULT_SETTINGS["SEARCH_PROFILES"]["precision"])
        precision["threshold"] = raw.get("THRESHOLD_VAL", precision["threshold"])
        precision["min_area"] = raw.get("MIN_TARGET", precision["min_area"])
        precision["max_area"] = raw.get("MAX_MAP_AREA", precision["max_area"])
        raw["SEARCH_PROFILES"] = {"precision": precision}

    merged = _deep_merge(DEFAULT_SETTINGS, raw)
    _validate_config(merged)

    if merged != raw:
        try:
            _write_config(merged)
        except OSError as exc:
            print(f"[config] не удалось обновить {CONFIG_FILE}: {exc}")
    return merged


def _write_config(data: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
    tmp.replace(CONFIG_FILE)


def save_config() -> None:
    """Сохраняет текущие настройки (профиль, тепловизор) на диск."""
    try:
        config["THERMAL_SETTINGS"] = dict(THERMAL_SETTINGS)
        config["ACTIVE_PROFILE"] = active_profile
        _write_config(config)
    except OSError as exc:
        print(f"[config] не удалось сохранить настройки: {exc}")


_SPECIAL_VK: Dict[str, int] = {
    "ESCAPE": win32con.VK_ESCAPE, "ESC": win32con.VK_ESCAPE,
    "F1": win32con.VK_F1, "F2": win32con.VK_F2, "F3": win32con.VK_F3,
    "F4": win32con.VK_F4, "F5": win32con.VK_F5, "F6": win32con.VK_F6,
    "F7": win32con.VK_F7, "F8": win32con.VK_F8, "F9": win32con.VK_F9,
    "F10": win32con.VK_F10, "F11": win32con.VK_F11, "F12": win32con.VK_F12,
    "SPACE": win32con.VK_SPACE, "SHIFT": win32con.VK_SHIFT,
    "CTRL": win32con.VK_CONTROL, "ALT": win32con.VK_MENU,
    "BACK": win32con.VK_BACK, "TAB": win32con.VK_TAB,
    "RETURN": win32con.VK_RETURN, "ENTER": win32con.VK_RETURN,
    "INSERT": win32con.VK_INSERT, "DELETE": win32con.VK_DELETE,
    "HOME": win32con.VK_HOME, "END": win32con.VK_END,
    "PGUP": win32con.VK_PRIOR, "PGDN": win32con.VK_NEXT,
    "UP": win32con.VK_UP, "DOWN": win32con.VK_DOWN,
    "LEFT": win32con.VK_LEFT, "RIGHT": win32con.VK_RIGHT,
    "NUMPAD0": win32con.VK_NUMPAD0, "NUMPAD1": win32con.VK_NUMPAD1,
    "NUMPAD2": win32con.VK_NUMPAD2, "NUMPAD3": win32con.VK_NUMPAD3,
    "NUMPAD4": win32con.VK_NUMPAD4, "NUMPAD5": win32con.VK_NUMPAD5,
    "NUMPAD6": win32con.VK_NUMPAD6, "NUMPAD7": win32con.VK_NUMPAD7,
    "NUMPAD8": win32con.VK_NUMPAD8, "NUMPAD9": win32con.VK_NUMPAD9,
    "ADD": win32con.VK_ADD, "SUBTRACT": win32con.VK_SUBTRACT,
    "MULTIPLY": win32con.VK_MULTIPLY, "DIVIDE": win32con.VK_DIVIDE,
    "DECIMAL": win32con.VK_DECIMAL,
    "LMB": win32con.VK_LBUTTON, "MOUSE1": win32con.VK_LBUTTON,
    "RMB": win32con.VK_RBUTTON, "MOUSE2": win32con.VK_RBUTTON,
    "MMB": win32con.VK_MBUTTON, "MOUSE3": win32con.VK_MBUTTON,
    "XBUTTON1": win32con.VK_XBUTTON1, "XBUTTON2": win32con.VK_XBUTTON2,
    "Ё": 0xC0, "`": 0xC0, "~": 0xC0,
}


def resolve_vk(key_name: str) -> int:
    """Переводит имя клавиши из конфига в виртуальный код Windows."""
    raw = str(key_name or "").strip()
    if not raw:
        raise ValueError("пустое имя клавиши")

    name = raw.upper()
    if name in _SPECIAL_VK:
        return _SPECIAL_VK[name]

    if len(raw) == 1:
        if "A" <= name <= "Z" or "0" <= name <= "9":
            return ord(name)
        vk = win32api.VkKeyScan(raw) & 0xFF
        if vk not in (0, 255):
            return vk

    raise ValueError(f"неизвестная клавиша: {raw!r}")


config: Dict[str, Any] = load_config()

SQUARE_SIZE = int(config["SQUARE_SIZE"])
SCAN_INTERVAL = float(config["SCAN_INTERVAL"])
ZOOM_HOLD_FACTOR = 2.0
ZOOM_LENS_FRACTION = 0.50
ZOOM_LENS_MIN = 360
ZOOM_LENS_MAX = 760
CAPTURE_RADIUS = 180
CENTER_DEADZONE = 8
CENTER_GAIN = 0.45
CENTER_MAX_STEP = 18
CENTER_INTERVAL = 0.02
THERMAL_SETTINGS: Dict[str, int] = config["THERMAL_SETTINGS"].copy()
LOCK_SETTINGS: Dict[str, Any] = config["LOCK_SETTINGS"].copy()
THERMAL_WINDOWS: List[str] = list(config["THERMAL_WINDOWS"])
active_profile: str = str(config["ACTIVE_PROFILE"])


def get_profile(name: Optional[str] = None) -> Dict[str, Any]:
    """Профиль поиска по имени (с безопасным фолбэком)."""
    profiles = config["SEARCH_PROFILES"]
    key = name or active_profile
    if key not in profiles:
        key = next(iter(profiles))
    return profiles[key]


def thermal_mode() -> str:
    """Текущее место показа тепловизора: window или overlay."""
    mode = str(config.get("THERMAL_MODE", "window")).strip().lower()
    return mode if mode in ("window", "overlay") else "window"


def interface_is_new() -> bool:
    """True для TV-интерфейса, False для обычного курсора."""
    return str(config.get("INTERFACE_MODE", "new")).strip().lower() != "normal"


LOCK_SIZE_MODES: Tuple[str, ...] = ("normal", "far", "near")
LOCK_MODE_TITLES: Dict[str, str] = {
    "normal": "NORMAL",
    "far": "FAR",
    "near": "NEAR",
}
LOCK_MODE_SIZE_KEYS: Dict[str, str] = {
    "normal": "template_size",
    "far": "far_size",
    "near": "near_size",
}
LOCK_MODE_DEFAULT_SIZES: Dict[str, int] = {
    "normal": 48,
    "far": 32,
    "near": 64,
}


def lock_size_mode() -> str:
    """Текущий режим размера хардлока: normal, far или near."""
    mode = str(config.get("LOCK_SIZE_MODE", "normal")).strip().lower()
    return mode if mode in LOCK_SIZE_MODES else "normal"


def lock_mode_title() -> str:
    """ASCII-подпись режима для RADAR_VIEW/оверлея."""
    return LOCK_MODE_TITLES[lock_size_mode()]


def lock_template_size(mode: Optional[str] = None) -> int:
    """Размер шаблона и рамки LOCK для текущего/заданного режима."""
    name = mode if mode in LOCK_SIZE_MODES else lock_size_mode()
    key = LOCK_MODE_SIZE_KEYS[name]
    default = LOCK_MODE_DEFAULT_SIZES[name]
    try:
        size = int(LOCK_SETTINGS.get(key, default))
    except (TypeError, ValueError):
        size = default
    if name == "far" and "template_size" in LOCK_SETTINGS:
        size = min(size, int(LOCK_SETTINGS["template_size"]))
    elif name == "near" and "template_size" in LOCK_SETTINGS:
        size = max(size, int(LOCK_SETTINGS["template_size"]))
    return max(16, min(160, size))


def center_use_keys() -> bool:
    """N корректирует удержанием A/D/W/S, а не движением курсора."""
    return bool(config.get("CENTER_USE_KEYS", True))


def center_auto_rmb() -> bool:
    """Старый режим: зажимать RMB, чтобы камера Roblox слушала мышь."""
    return bool(config.get("CENTER_AUTO_RMB", False))


_binds_cfg: Dict[str, Any] = config["BINDS"]


def _bind(name: str, default: str) -> int:
    """Достаёт VK из BINDS; при ошибке использует дефолтную клавишу."""
    value = str(_binds_cfg.get(name, default))
    try:
        return resolve_vk(value)
    except ValueError:
        print(f"[binds] неверная клавиша {name}={value!r}, использую {default!r}")
        return resolve_vk(default)


VK_SCAN = _bind("key_scan", "F")
VK_THERMAL = _bind("key_thermal", "Q")
VK_FULLSCAN = _bind("key_fullscan", "H")
VK_THERMAL_MODE = _bind("key_thermal_mode", "Y")
VK_TRACK = _bind("key_tracking", "V")
VK_PROFILE = _bind("key_profile", "G")
VK_ZOOM = _bind("key_zoom", "Z")
VK_ZOOM_HOLD = _bind("key_zoom_hold", "RMB")
VK_CAPTURE = _bind("key_capture", "T")
VK_LOCK_MODE = _bind("key_lock_mode", "X")
VK_CENTER_HOLD = _bind("key_center_hold", "N")
VK_EXIT = _bind("key_exit", "ESCAPE")
VK_HIDE = _bind("key_hide", "Ё")

BIND_LABELS: Dict[str, str] = {
    name: str(_binds_cfg.get(f"key_{name}", default)).upper()
    for name, default in (
        ("scan", "F"), ("thermal", "Q"), ("fullscan", "H"),
        ("thermal_mode", "Y"), ("tracking", "V"), ("profile", "G"),
        ("zoom", "Z"), ("zoom_hold", "RMB"), ("capture", "T"),
        ("lock_mode", "X"), ("center_hold", "N"),
        ("exit", "ESCAPE"), ("hide", "Ё"),
    )
}

def _cv2_label(label: str) -> str:
    if label == "Ё":
        return "YO"
    if label == "ё":
        return "yo"
    return label.encode("ascii", "replace").decode()


CV2_BIND_LABELS: Dict[str, str] = {
    name: _cv2_label(label) for name, label in BIND_LABELS.items()
}


class KeyState:
    """Состояние клавиш по опросу GetAsyncKeyState (единый механизм ввода)."""

    def __init__(self) -> None:
        self._prev: Dict[int, bool] = {}

    @staticmethod
    def pressed(vk: int) -> bool:
        """True, пока клавиша удерживается."""
        return bool(win32api.GetAsyncKeyState(vk) & 0x8000)

    def just_pressed(self, vk: int) -> bool:
        """True один раз в момент нажатия (front edge)."""
        now = self.pressed(vk)
        was = self._prev.get(vk, False)
        self._prev[vk] = now
        return now and not was


keys = KeyState()


shared_data: Dict[str, Any] = {
    "active": False,
    "tracking_mode": False,
    "screen_center": (0, 0),
    "targets": [],
    "main_target": None,
    "lock_target": None,
    "thermal_active": False,
    "fullscan_active": False,
    "full_targets": [],
    "full_main": None,
    "zoom_hold": False,
    "center_hold": False,
    "client_center": (0, 0),
}


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


_gdi32 = ctypes.windll.gdi32
_user32 = ctypes.windll.user32

_gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
_gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi32.DeleteObject.restype = ctypes.c_int
_gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
_gdi32.DeleteDC.restype = ctypes.c_int
_gdi32.BitBlt.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
_gdi32.BitBlt.restype = ctypes.c_int
_gdi32.GetDIBits.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(BITMAPINFOHEADER), ctypes.c_uint,
]
_gdi32.GetDIBits.restype = ctypes.c_int
_user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
_user32.PrintWindow.restype = ctypes.c_int

PW_CLIENT_ONLY = 0x1
PW_RENDER_FULL_CONTENT = 0x2
SRCCOPY_CAPTUREBLT = 0x40CC0020


_roblox_hwnd: Optional[int] = None
_roblox_hwnd_at = 0.0
_roblox_hwnd_ttl = 2.0


def find_roblox_hwnd() -> Optional[int]:
    """Ищет видимое окно Roblox по подстроке заголовка.

    Хендл кэшируется: перечисление всех окон 20 раз в секунду не нужно.
    Окна своего процесса пропускаются — GetWindowText для них отправляет
    сообщение в GUI-поток и может заблокировать рабочий поток (дедлок при
    выходе, когда GUI занят ожиданием join).
    """
    global _roblox_hwnd, _roblox_hwnd_at

    now = time.perf_counter()
    if now - _roblox_hwnd_at < _roblox_hwnd_ttl:
        if _roblox_hwnd is None:
            return None
        if not win32gui.IsIconic(_roblox_hwnd) and win32gui.IsWindow(_roblox_hwnd):
            return _roblox_hwnd

    found: List[int] = []
    own_pid = os.getpid()

    def _callback(hwnd: int, _param: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == own_pid:
                return True
            title = win32gui.GetWindowText(hwnd).lower()
        except Exception:
            return True
        if not title:
            return True
        for needle in THERMAL_WINDOWS:
            if needle.lower() in title:
                found.append(hwnd)
                return False
        return True

    try:
        win32gui.EnumWindows(_callback, None)
    except Exception as exc:
        print(f"[capture] EnumWindows: {exc}")

    _roblox_hwnd = found[0] if found else None
    _roblox_hwnd_at = now
    return _roblox_hwnd


def capture_roblox_window() -> Optional[Tuple[np.ndarray, Tuple[int, int]]]:
    """Снимок клиентской области Roblox в grayscale + экранная позиция окна."""
    hwnd = find_roblox_hwnd()
    if not hwnd:
        return None

    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    width, height = cr - cl, cb - ct
    if width <= 0 or height <= 0:
        return None

    origin = win32gui.ClientToScreen(hwnd, (0, 0))
    window_dc = win32gui.GetDC(hwnd)
    mem_dc: Optional[int] = None
    bitmap: Optional[int] = None
    old_bitmap: Optional[int] = None

    try:
        mem_dc = _gdi32.CreateCompatibleDC(window_dc)
        bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
        if not mem_dc or not bitmap:
            return None
        old_bitmap = _gdi32.SelectObject(mem_dc, bitmap)

        flags = PW_CLIENT_ONLY | PW_RENDER_FULL_CONTENT
        if not _user32.PrintWindow(hwnd, mem_dc, flags):
            _gdi32.BitBlt(mem_dc, 0, 0, width, height, window_dc, 0, 0, SRCCOPY_CAPTUREBLT)

        bmi = BITMAPINFOHEADER(
            biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,
            biPlanes=1, biBitCount=32, biCompression=0,
        )
        buffer = (ctypes.c_ubyte * (width * height * 4))()
        if _gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(bmi), 0) == 0:
            return None

        image = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 4))
        gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        return gray, (int(origin[0]), int(origin[1]))
    finally:
        if mem_dc:
            if old_bitmap:
                _gdi32.SelectObject(mem_dc, old_bitmap)
            if bitmap:
                _gdi32.DeleteObject(bitmap)
            _gdi32.DeleteDC(mem_dc)
        win32gui.ReleaseDC(hwnd, window_dc)


Rect = Tuple[int, int, int, int]


class TargetTracker:
    """Подтверждает цели по нескольким кадрам (защита от помех).

    Режим "precision" (min_hits=1) отдаёт всё сразу, режим "noise" требует,
    чтобы объект стабильно появлялся рядом на нескольких сканах. Допускается
    ``max_misses`` пропущенных сканов подряд: цель может мигнуть/пропасть на
    кадре, но прогресс подтверждения не сбрасывается.
    """

    def __init__(self) -> None:
        self._hits: List[Tuple[Rect, int, int]] = []

    @staticmethod
    def _center(rect: Rect) -> Tuple[int, int]:
        return rect[0] + rect[2] // 2, rect[1] + rect[3] // 2

    def update(self, rects: List[Rect], profile: Dict[str, Any]) -> List[Rect]:
        required = max(1, int(profile.get("min_hits", 1)))
        if required <= 1:
            self._hits = [(rect, 1, 0) for rect in rects[:200]]
            return list(rects)

        max_dist = max(16, int(profile.get("match_dist", 0)))
        max_misses = max(0, int(profile.get("max_misses", 0)))
        used = [False] * len(rects)
        confirmed: List[Rect] = []
        next_hits: List[Tuple[Rect, int, int]] = []

        for old_rect, hits, misses in self._hits:
            index = self._find_match(old_rect, rects, used, max_dist)
            if index is None:
                misses += 1
                if misses <= max_misses and hits > 0:
                    next_hits.append((old_rect, hits, misses))
                continue
            used[index] = True
            hits += 1
            next_hits.append((rects[index], hits, 0))
            if hits >= required:
                confirmed.append(rects[index])

        for index, rect in enumerate(rects):
            if not used[index]:
                next_hits.append((rect, 1, 0))

        self._hits = next_hits[:200]
        return confirmed

    def _find_match(
        self, old_rect: Rect, rects: List[Rect], used: List[bool], max_dist: int
    ) -> Optional[int]:
        ox, oy = self._center(old_rect)
        best: Optional[int] = None
        best_dist = float(max_dist)
        for index, rect in enumerate(rects):
            if used[index]:
                continue
            x, y = self._center(rect)
            dist = ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5
            if dist <= best_dist:
                best, best_dist = index, dist
        return best


def analyze_motion(
    diff: np.ndarray, profile: Dict[str, Any]
) -> List[Tuple[int, int, int, int]]:
    """Ищет цели по карте разницы кадров.

    Возвращает прямоугольники в координатах переданной карты (без смещения
    на экран). Вся настройка берётся из профиля:

    - ``threshold``/``blur`` — базовая чувствительность;
    - ``adaptive`` — порог поднимается на подвижном фоне (поворот камеры),
      но никогда не опускается ниже базового;
    - ``max_motion_ratio`` — если изменилась слишком большая часть кадра,
      скан пропускается (это поворот камеры, а не цель);
    - ``open`` — убирает одиночные пиксели шума до дилатации;
    - ``min_seed`` — минимальная площадь пятна до дилатации (мелкие пятна
      не разрастаются в ложные блобы);
    - ``min_area``/``max_area``/``min_fill``/``max_aspect`` — фильтры блоба.
    """
    blur = int(profile.get("blur", 0))
    if blur > 1:
        diff = cv2.GaussianBlur(diff, (blur, blur), 0)

    threshold = int(profile["threshold"])
    if profile.get("adaptive"):
        high = float(np.percentile(diff, 98))
        threshold = max(threshold, min(255, int(high * 0.5)))

    _, thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)

    motion_ratio = cv2.countNonZero(thresh) / float(thresh.size)
    if motion_ratio > float(profile.get("max_motion_ratio", 1.0)):
        return []

    open_size = int(profile.get("open", 0))
    if open_size > 1:
        thresh = cv2.morphologyEx(
            thresh, cv2.MORPH_OPEN, np.ones((open_size, open_size), dtype=np.uint8)
        )

    min_seed = int(profile.get("min_seed", 0))
    if min_seed > 0:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(thresh, connectivity=8)
        keep = stats[:, cv2.CC_STAT_AREA] >= min_seed
        keep[0] = False
        lut = np.zeros(count, dtype=np.uint8)
        lut[keep] = 255
        thresh = lut[labels]

    kernel_size = int(profile["dilate"])
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    closed = cv2.morphologyEx(cv2.dilate(thresh, kernel), cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = int(profile["min_area"])
    max_area = int(profile["max_area"])
    min_fill = float(profile.get("min_fill", 0.0))
    max_aspect = float(profile.get("max_aspect", 0.0))

    targets: List[Tuple[int, int, int, int]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not (min_area < area < max_area):
            continue
        tx, ty, tw, th = cv2.boundingRect(contour)
        if tw <= 0 or th <= 0:
            continue
        if min_fill > 0.0 and area / float(tw * th) < min_fill:
            continue
        if max_aspect > 0.0 and max(tw, th) / float(min(tw, th)) > max_aspect:
            continue
        targets.append((tx, ty, tw, th))
    return targets


class ScanWorker(threading.Thread):
    """Сканирование в отдельном потоке: UI больше не спит в time.sleep()."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="radar-scan")
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._active_evt = threading.Event()
        self._profile: Dict[str, Any] = dict(get_profile())
        self._size = SQUARE_SIZE
        self._interval = SCAN_INTERVAL
        self._tracker = TargetTracker()
        self._targets: List[Rect] = []
        self._main: Optional[Rect] = None
        self._motion: Optional[np.ndarray] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._prev_origin: Optional[Tuple[int, int]] = None

        self._manual_template: Optional[np.ndarray] = None
        self._manual_template_bank: List[np.ndarray] = []
        self._manual_center: Tuple[float, float] = (0.0, 0.0)
        self._manual_last_seen: Optional[Tuple[float, float]] = None
        self._manual_velocity: Tuple[float, float] = (0.0, 0.0)
        self._manual_misses = 0
        self._manual_score = 0.0
        self._manual_rect: Optional[Rect] = None
        self._manual_lost_at: Optional[float] = None
        self._manual_generation = 0

    def configure(
        self,
        profile: Optional[Dict[str, Any]] = None,
        size: Optional[int] = None,
        interval: Optional[float] = None,
    ) -> None:
        with self._lock:
            if profile is not None:
                self._profile = dict(profile)
                self._tracker = TargetTracker()
            if size is not None:
                self._size = max(64, int(size))
            if interval is not None:
                self._interval = max(0.005, float(interval))

    def set_active(self, active: bool) -> None:
        if active == self._active_evt.is_set():
            return
        if active:
            self._active_evt.set()
        else:
            self._active_evt.clear()
            self._prev_gray = None
            self._prev_origin = None
            with self._lock:
                self._targets = []
                self._main = None
                self._motion = None

    def set_manual_lock(
        self,
        template: np.ndarray,
        center: Tuple[float, float],
        rect: Optional[Rect] = None,
    ) -> None:
        """Захватить цель по шаблону из точки под курсором."""
        with self._lock:
            self._manual_template = template.copy()
            self._manual_template_bank = []
            self._manual_center = (float(center[0]), float(center[1]))
            self._manual_last_seen = (float(center[0]), float(center[1]))
            self._manual_velocity = (0.0, 0.0)
            self._manual_misses = 0
            self._manual_score = 1.0
            self._manual_rect = rect
            self._manual_lost_at = None
            self._manual_generation += 1

    def clear_manual_lock(self) -> None:
        with self._lock:
            self._manual_template = None
            self._manual_template_bank = []
            self._manual_rect = None
            self._manual_last_seen = None
            self._manual_score = 0.0
            self._manual_lost_at = None
            self._manual_generation += 1

    def has_manual_lock(self) -> bool:
        with self._lock:
            return self._manual_template is not None

    def snapshot(
        self,
    ) -> Tuple[List[Rect], Optional[Rect], Optional[np.ndarray], Optional[Rect]]:
        with self._lock:
            return self._targets, self._main, self._motion, self._manual_rect

    def manual_lock_rect(self) -> Optional[Rect]:
        """Последняя рамка захвата; сохраняется и при выключенном поиске."""
        with self._lock:
            return self._manual_rect

    def stop(self) -> None:
        self._stop_evt.set()
        self._active_evt.set()
        self.join(1.5)

    def run(self) -> None:
        while not self._stop_evt.is_set():
            if not self._active_evt.is_set():
                with self._lock:
                    has_lock = self._manual_template is not None
                if not has_lock:
                    self._active_evt.wait(0.05)
                    continue
                started = time.perf_counter()
                try:
                    tracked = self._track_manual_only()
                except Exception as exc:
                    print(f"[lock] {exc}")
                    tracked = False
                delay = self._interval - (time.perf_counter() - started)
                if not tracked:
                    delay = max(delay, 0.1)
                if delay > 0:
                    self._stop_evt.wait(delay)
                continue

            with self._lock:
                profile = dict(self._profile)
                size = self._size
                interval = self._interval

            started = time.perf_counter()
            try:
                targets, motion = self._scan(profile, size)
            except Exception as exc:
                print(f"[scan] {exc}")
                self._prev_gray = None
                self._prev_origin = None
                self._stop_evt.wait(0.1)
                continue

            with self._lock:
                confirmed = self._tracker.update(targets, profile)
                self._targets = confirmed
                self._main = (
                    max(confirmed, key=lambda rect: rect[2] * rect[3]) if confirmed else None
                )
                self._motion = motion

            delay = interval - (time.perf_counter() - started)
            if delay > 0:
                self._stop_evt.wait(delay)

    def _track_manual_only(self) -> bool:
        """Ищет только ручной захват, без радарного детектора движения."""
        with self._lock:
            if self._manual_template is None:
                return False
        captured = capture_roblox_window()
        if captured is None:
            return False
        gray, origin = captured
        shared_data["client_center"] = (
            origin[0] + gray.shape[1] // 2,
            origin[1] + gray.shape[0] // 2,
        )
        self._update_manual_lock(gray, origin)
        return True

    def _scan(
        self, profile: Dict[str, Any], size: int
    ) -> Tuple[List[Rect], np.ndarray]:
        """Один проход по окну Roblox в квадрате вокруг курсора.

        Захват идёт через PrintWindow, поэтому в кадр не попадают другие окна,
        оверлей радара и рабочий стол. Предыдущий кадр переиспользуется:
        на скан нужен один снимок окна, а не два.
        """
        blank = np.zeros((size, size), dtype=np.uint8)
        captured = capture_roblox_window()
        if captured is None:
            self._prev_gray = None
            self._prev_origin = None
            return [], blank

        gray, origin = captured
        shared_data["client_center"] = (
            origin[0] + gray.shape[1] // 2,
            origin[1] + gray.shape[0] // 2,
        )
        prev_gray = self._prev_gray
        prev_origin = self._prev_origin
        self._prev_gray = gray
        self._prev_origin = origin

        self._update_manual_lock(gray, origin)

        client_h, client_w = gray.shape
        mx, my = win32api.GetCursorPos()
        half = size // 2
        left = mx - origin[0] - half
        top = my - origin[1] - half
        x0 = max(0, left)
        y0 = max(0, top)
        x1 = min(client_w, left + size)
        y1 = min(client_h, top + size)
        if x1 <= x0 or y1 <= y0:
            return [], blank

        if prev_gray is None or prev_origin != origin or prev_gray.shape != gray.shape:
            return [], blank

        raw = cv2.absdiff(prev_gray[y0:y1, x0:x1], gray[y0:y1, x0:x1])

        motion = blank.copy()
        motion[y0 - top:y1 - top, x0 - left:x1 - left] = raw

        diff = raw
        targets: List[Rect] = []
        for tx, ty, tw, th in analyze_motion(diff, profile):
            targets.append((origin[0] + x0 + tx, origin[1] + y0 + ty, tw, th))
        return targets, motion


    @staticmethod
    def _patch_at(
        gray: np.ndarray, center: Tuple[float, float], tw: int, th: int
    ) -> Optional[np.ndarray]:
        h, w = gray.shape
        if tw > w or th > h:
            return None
        left = max(0, min(w - tw, int(round(center[0] - tw / 2.0))))
        top = max(0, min(h - th, int(round(center[1] - th / 2.0))))
        return gray[top:top + th, left:left + tw].copy()

    @staticmethod
    def _match_template(
        gray: np.ndarray,
        template: np.ndarray,
        center: Tuple[float, float],
        margin: int,
        crop: float = 1.0,
    ) -> Optional[Tuple[Tuple[float, float], float]]:
        """Сопоставляет шаблон; crop<1 сравнивает только центр цели.

        Фон вокруг цели часто меняется (трава, стена, небо), а вместе с ним
        меняется и обычный 48x48-шаблон. Центральный кроп оставляет только
        саму цель, поэтому LOCK не привязан к одному фону.
        """
        h, w = gray.shape
        th, tw = template.shape[:2]
        if crop < 0.999:
            cw = max(8, int(round(tw * crop)))
            ch = max(8, int(round(th * crop)))
            cx0 = (tw - cw) // 2
            cy0 = (th - ch) // 2
            template = template[cy0:cy0 + ch, cx0:cx0 + cw]
            th, tw = template.shape[:2]
        if w < tw or h < th:
            return None
        left = max(0, int(round(center[0] - tw / 2.0 - margin)))
        top = max(0, int(round(center[1] - th / 2.0 - margin)))
        right = min(w, int(round(center[0] + tw / 2.0 + margin)) + 1)
        bottom = min(h, int(round(center[1] + th / 2.0 + margin)) + 1)
        if right - left < tw or bottom - top < th:
            return None
        result = cv2.matchTemplate(gray[top:bottom, left:right], template, cv2.TM_CCOEFF_NORMED)
        result = np.nan_to_num(result, nan=-1.0, posinf=1.0, neginf=-1.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        matched = (
            left + max_loc[0] + tw / 2.0,
            top + max_loc[1] + th / 2.0,
        )
        return matched, float(max_val)

    def _match_any_template(
        self,
        gray: np.ndarray,
        templates: List[np.ndarray],
        center: Tuple[float, float],
        margin: int,
        crops: Tuple[float, ...] = (1.0, 0.75, 0.55, 0.40),
        min_score: Optional[float] = None,
    ) -> Optional[Tuple[Tuple[float, float], float, int]]:
        """Лучшее совпадение по банку эталонов и нескольким кропам.

        Возвращает (центр, score, индекс): индекс 0 — основной эталон,
        остальные — дополнительные из банка. Если min_score задан, поиск
        прекращается на первом уверенном совпадении — это сильно экономит
        CPU в обычном режиме и при широком/lost-поиске.
        """
        best: Optional[Tuple[Tuple[float, float], float, int]] = None
        for index, template in enumerate(templates):
            for crop in crops:
                found = self._match_template(gray, template, center, margin, crop)
                if found is None:
                    continue
                if best is None or found[1] > best[1]:
                    best = (found[0], found[1], index)
                    if min_score is not None and found[1] >= min_score:
                        return best
        return best

    @staticmethod
    def _blend_center(
        old: np.ndarray, new: np.ndarray, alpha: float = 0.2
    ) -> np.ndarray:
        """Обновляет только центр эталона, чтобы фон не «впечатывался»."""
        if old.shape != new.shape:
            return old.copy()
        h, w = old.shape[:2]
        ch = max(8, int(round(h * 0.60)))
        cw = max(8, int(round(w * 0.60)))
        y0 = (h - ch) // 2
        x0 = (w - cw) // 2
        result = old.copy()
        result[y0:y0 + ch, x0:x0 + cw] = cv2.addWeighted(
            old[y0:y0 + ch, x0:x0 + cw], 1.0 - alpha,
            new[y0:y0 + ch, x0:x0 + cw], alpha, 0,
        )
        return result

    @staticmethod
    def _patch_difference(a: np.ndarray, b: np.ndarray) -> float:
        if a.shape != b.shape:
            return float("inf")
        return float(np.mean(cv2.absdiff(a, b)))

    def _update_manual_lock(self, gray: np.ndarray, origin: Tuple[int, int]) -> None:
        """Ведёт цель по шаблону и не сдаётся сразу после промахов.

        Сначала поиск идёт рядом с предсказанной позицией (coast), затем шире.
        Когда обычный лимит промахов исчерпан, ещё lost_timeout секунд шаблон
        ищется в радиусе lost_margin вокруг последней подтверждённой позиции —
        так захват переживает уход цели за укрытие или резкий рывок.
        """
        now = time.perf_counter()
        with self._lock:
            template = self._manual_template
            if template is None:
                return
            bank = [
                item for item in self._manual_template_bank
                if item.shape == template.shape
            ]
            center = self._manual_center
            last_seen = self._manual_last_seen or center
            velocity = self._manual_velocity
            misses = self._manual_misses
            lost_at = self._manual_lost_at
            generation = self._manual_generation

        th, tw = template.shape[:2]
        frame_h, frame_w = gray.shape
        if tw > frame_w or th > frame_h:
            with self._lock:
                if generation == self._manual_generation:
                    self._manual_template = None
                    self._manual_template_bank = []
                    self._manual_rect = None
                    self._manual_last_seen = None
                    self._manual_lost_at = None
            return

        templates = [template] + bank
        recent_templates = [template] + bank[-2:]
        predicted = (center[0] + velocity[0], center[1] + velocity[1])
        settings = LOCK_SETTINGS
        max_misses = int(settings["max_misses"])
        match = self._match_any_template(
            gray, templates, predicted, int(settings["search_margin"]),
            min_score=max(float(settings["match_min"]), 0.80),
        )
        accepted = match is not None and match[1] >= float(settings["match_min"])
        lost_mode = not accepted and misses + 1 > max_misses
        matched_via_lost = False

        if not accepted and not lost_mode and misses % 2 == 0:
            wide = self._match_any_template(
                gray, recent_templates, predicted, int(settings["wide_margin"]),
                crops=(0.75, 0.55, 0.40),
                min_score=float(settings["wide_match_min"]),
            )
            if (
                wide is not None
                and wide[1] >= float(settings["wide_match_min"])
                and (match is None or wide[1] > match[1] + 0.05)
            ):
                match = wide
                accepted = True
                lost_mode = False

        if not accepted and lost_mode and misses % 2 == 0:
            if lost_at is None:
                lost_at = now
            lost = self._match_any_template(
                gray, recent_templates, last_seen, int(settings["lost_margin"]),
                crops=(0.75, 0.55, 0.40),
                min_score=float(settings["lost_match_min"]),
            )
            if lost is not None and lost[1] >= float(settings["lost_match_min"]):
                match = lost
                accepted = True
                matched_via_lost = True

        if accepted and match is not None:
            new_center, score, matched_index = match
            dx = new_center[0] - center[0]
            dy = new_center[1] - center[1]
            jump = (dx * dx + dy * dy) ** 0.5
            if matched_via_lost or jump > float(settings["wide_margin"]):
                velocity = (0.0, 0.0)
            else:
                vx = 0.5 * velocity[0] + 0.5 * dx
                vy = 0.5 * velocity[1] + 0.5 * dy
                limit = 60.0
                velocity = (
                    max(-limit, min(limit, vx)),
                    max(-limit, min(limit, vy)),
                )
            misses = 0
            lost_at = None
            last_seen = new_center
            patch = self._patch_at(gray, new_center, tw, th)

            update_ok = score >= float(settings["update_score"]) and (
                not matched_via_lost or score >= 0.90
            )
            if patch is not None and update_ok:
                if matched_index == 0:
                    template = self._blend_center(template, patch)
                elif 0 < matched_index <= len(bank):
                    bank[matched_index - 1] = self._blend_center(
                        bank[matched_index - 1], patch
                    )

            bank_size = int(settings.get("template_bank_size", 4))
            if (
                patch is not None
                and bank_size > 0
                and score >= float(settings.get("bank_add_score", 0.72))
                and (not matched_via_lost or score >= 0.80)
            ):
                diff_min = float(settings.get("bank_diff_min", 10.0))
                known = [template] + bank
                if all(
                    self._patch_difference(patch, item) > diff_min
                    for item in known
                ):
                    if len(bank) >= bank_size:
                        bank.pop(0)
                    bank.append(patch.copy())
        else:
            misses += 1
            if lost_mode:
                new_center = last_seen
                velocity = (0.0, 0.0)
            else:
                new_center = predicted
                velocity = (velocity[0] * 0.9, velocity[1] * 0.9)
                cap = float(settings["wide_margin"])
                dx = new_center[0] - last_seen[0]
                dy = new_center[1] - last_seen[1]
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > cap and dist > 0.0:
                    new_center = (
                        last_seen[0] + dx * cap / dist,
                        last_seen[1] + dy * cap / dist,
                    )
            score = match[1] if match is not None else 0.0

        lost_expired = (
            lost_at is not None
            and now - lost_at > float(settings["lost_timeout"])
        )
        if misses > max_misses and lost_expired:
            template = None
            bank = []
            rect: Optional[Rect] = None
            score = 0.0
            last_seen = None
            lost_at = None
        else:
            left = max(0, min(frame_w - tw, int(round(new_center[0] - tw / 2.0))))
            top = max(0, min(frame_h - th, int(round(new_center[1] - th / 2.0))))
            new_center = (left + tw / 2.0, top + th / 2.0)
            rect = (origin[0] + left, origin[1] + top, tw, th)

        with self._lock:
            if generation != self._manual_generation:
                return
            self._manual_template = template
            self._manual_template_bank = bank
            self._manual_center = new_center
            self._manual_last_seen = last_seen
            self._manual_velocity = velocity
            self._manual_misses = misses
            self._manual_score = score
            self._manual_rect = rect
            self._manual_lost_at = lost_at


class FullViewSnapshot(NamedTuple):
    """Один кадр полного окна Roblox для RADAR_VIEW.

    ``thermal`` — чёрно-белая тепловая карта (BGR) для RADAR_VIEW и
    TV-прицела, ``overlay`` — прозрачный BGRA-слой для полноэкранного
    оверлея, ``raw`` — текущий grayscale-кадр Roblox (для цифрового зума),
    ``motion`` — сырая карта разницы кадров (grayscale). ``targets`` уже в
    экранных координатах, ``origin``/``size`` нужны для пересчёта рамок в
    координаты окна.
    """

    thermal: Optional[np.ndarray]
    overlay: Optional[np.ndarray]
    raw: Optional[np.ndarray]
    motion: Optional[np.ndarray]
    targets: List[Rect]
    main: Optional[Rect]
    origin: Tuple[int, int]
    size: Tuple[int, int]
    found: bool
    count: int


class ThermalWorker(threading.Thread):
    """Полное окно Roblox: тепловая карта, оверлей и полный поиск целей.

    В режиме ``window`` воркер отдаёт BGR-карту для RADAR_VIEW, в режиме
    ``overlay`` — прозрачный BGRA-слой для полноэкранного оверлея. Один захват
    кадра используется и для отображения, и для поиска целей, поэтому Q и H
    можно включать независимо друг от друга без второго прохода PrintWindow.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="radar-fullview")
        self._stop_evt = threading.Event()
        self._active_evt = threading.Event()
        self._lock = threading.RLock()
        self._snapshot = self._empty()
        self._thermal_active = False
        self._fullscan_active = False
        self._zoom_active = False
        self._prev: Optional[np.ndarray] = None
        self._profile: Dict[str, Any] = dict(get_profile())
        self._tracker = TargetTracker()
        self._targets: List[Rect] = []
        self._main: Optional[Rect] = None

    @staticmethod
    def _empty(
        found: bool = False,
        origin: Tuple[int, int] = (0, 0),
        size: Tuple[int, int] = (0, 0),
    ) -> FullViewSnapshot:
        return FullViewSnapshot(
            None, None, None, None, [], None, origin, size, found, 0
        )

    def configure(self, profile: Dict[str, Any]) -> None:
        """Смена профиля поиска: старые подтверждения больше не актуальны."""
        with self._lock:
            self._profile = dict(profile)
            self._tracker = TargetTracker()
            self._targets = []
            self._main = None

    def set_thermal(self, active: bool) -> None:
        with self._lock:
            self._thermal_active = bool(active)
            if not active:
                self._snapshot = self._snapshot._replace(
                    thermal=None, overlay=None
                )
        self._refresh_active()

    def set_fullscan(self, active: bool) -> None:
        with self._lock:
            self._fullscan_active = bool(active)
            if not active:
                self._targets = []
                self._main = None
                self._tracker = TargetTracker()
                self._snapshot = self._snapshot._replace(
                    targets=[], main=None, motion=None
                )
        self._refresh_active()

    def set_zoom(self, active: bool) -> None:
        """Держит воркер активным, пока удерживается RMB-прицел.

        В этом режиме нужен только ``raw``-кадр, но он же используется и
        тепловизором, поэтому отдельный захват окна не требуется.
        """
        active = bool(active)
        with self._lock:
            if self._zoom_active == active:
                return
            self._zoom_active = active
        self._refresh_active()

    def set_active(self, active: bool) -> None:
        """Совместимость со старым API: включает только тепловую карту."""
        self.set_thermal(active)

    def _refresh_active(self) -> None:
        with self._lock:
            active = (
                self._thermal_active
                or self._fullscan_active
                or self._zoom_active
            )
        if active:
            self._active_evt.set()
            return

        self._active_evt.clear()
        self._prev = None
        with self._lock:
            self._snapshot = self._empty()
            self._targets = []
            self._main = None
            self._tracker = TargetTracker()

    def snapshot(self) -> FullViewSnapshot:
        with self._lock:
            snap = self._snapshot
            return snap._replace(targets=list(snap.targets))

    def stop(self) -> None:
        self._stop_evt.set()
        self._active_evt.set()
        self.join(1.5)

    def run(self) -> None:
        while not self._stop_evt.is_set():
            if not self._active_evt.wait(0.1):
                continue

            started = time.perf_counter()
            try:
                snap = self._scan_once()
            except Exception as exc:
                print(f"[fullview] {exc}")
                snap = self._empty()
                self._prev = None
            with self._lock:
                if not self._fullscan_active:
                    snap = snap._replace(targets=[], main=None, motion=None)
                if not self._thermal_active:
                    snap = snap._replace(thermal=None, overlay=None)
                elif thermal_mode() == "window":
                    snap = snap._replace(overlay=None)
                self._snapshot = snap

            delay = 0.05 - (time.perf_counter() - started)
            if delay > 0:
                self._stop_evt.wait(delay)

    def _scan_once(self) -> FullViewSnapshot:
        with self._lock:
            thermal_on = self._thermal_active
            fullscan_on = self._fullscan_active
            profile = dict(self._profile)
        mode = thermal_mode()
        want_window = thermal_on
        want_overlay = thermal_on and mode == "overlay"

        captured = capture_roblox_window()
        if captured is None:
            self._prev = None
            return self._empty()

        gray, origin = captured
        height, width = gray.shape
        prev = self._prev
        self._prev = gray

        if prev is None or prev.shape != gray.shape:
            thermal = self._thermal_bg(width, height) if want_window else None
            overlay = (
                np.zeros((height, width, 4), dtype=np.uint8)
                if want_overlay
                else None
            )
            return FullViewSnapshot(
                thermal, overlay, gray, None, [], None, origin,
                (width, height), True, 0,
            )

        diff = cv2.absdiff(prev, gray)
        thermal: Optional[np.ndarray] = None
        overlay: Optional[np.ndarray] = None
        count = 0
        if thermal_on:
            thermal, overlay, count = self._thermal_image(
                diff, want_window, want_overlay
            )

        targets: List[Rect] = []
        main: Optional[Rect] = None
        if fullscan_on:
            raw = analyze_motion(diff, profile)
            screen_rects = [
                (origin[0] + x, origin[1] + y, tw, th)
                for x, y, tw, th in raw
            ]
            with self._lock:
                targets = self._tracker.update(screen_rects, profile)
                self._targets = list(targets)
                main = (
                    max(targets, key=lambda rect: rect[2] * rect[3])
                    if targets
                    else None
                )
                self._main = main
        else:
            with self._lock:
                self._targets = []
                self._main = None

        motion = diff if fullscan_on else None
        return FullViewSnapshot(
            thermal, overlay, gray, motion, list(targets), main, origin,
            (width, height), True, count,
        )

    @staticmethod
    def _thermal_bg(width: int, height: int) -> np.ndarray:
        """Тёмный фон тепловой карты (цвет BGR)."""
        bg = max(0, min(80, int(THERMAL_SETTINGS.get("bg_alpha", 140)) // 4))
        return np.full((height, width, 3), bg, dtype=np.uint8)

    @staticmethod
    def _thermal_image(
        diff: np.ndarray, want_window: bool, want_overlay: bool
    ) -> Tuple[
        Optional[np.ndarray], Optional[np.ndarray], int
    ]:
        """Чёрно-белая (white-hot) тепловая карта всего окна.

        ``want_window`` — BGR-карта для RADAR_VIEW и TV-прицела,
        ``want_overlay`` — BGRA-слой для полноэкранного оверлея (фон
        прозрачный). Порог, шаг выборки и размер точки берутся из
        THERMAL_SETTINGS.
        """
        height, width = diff.shape
        _, mask = cv2.threshold(
            diff, int(THERMAL_SETTINGS["threshold"]), 255, cv2.THRESH_BINARY
        )

        step = max(1, int(THERMAL_SETTINGS["sample_step"]))
        if step > 1:
            sampled = np.zeros_like(mask)
            sampled[::step, ::step] = mask[::step, ::step]
            mask = sampled

        dot_size = max(1, int(THERMAL_SETTINGS["dot_size"]))
        if dot_size > 1:
            mask = cv2.dilate(
                mask, np.ones((dot_size, dot_size), dtype=np.uint8)
            )

        count = int(cv2.countNonZero(mask))
        heat: Optional[np.ndarray] = None
        if count:
            hot = cv2.bitwise_and(diff, diff, mask=mask)
            gain = max(1, int(config.get("MOTION_MAP_GAIN", 8)))
            hot = cv2.convertScaleAbs(hot, alpha=gain, beta=35)
            heat = cv2.cvtColor(hot, cv2.COLOR_GRAY2BGR)

        thermal: Optional[np.ndarray] = None
        if want_window:
            thermal = ThermalWorker._thermal_bg(width, height)
            if heat is not None:
                thermal[mask != 0] = heat[mask != 0]

        overlay: Optional[np.ndarray] = None
        if want_overlay:
            overlay = np.zeros((height, width, 4), dtype=np.uint8)
            if heat is not None:
                overlay[mask != 0, :3] = heat[mask != 0]
                overlay[mask != 0, 3] = 255

        return thermal, overlay, count


class Overlay(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.sw = win32api.GetSystemMetrics(0)
        self.sh = win32api.GetSystemMetrics(1)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, self.sw, self.sh)
        shared_data["screen_center"] = (self.sw // 2, self.sh // 2)

        self.is_hidden = False
        self._closing = False
        self._prev_active = False
        self._prev_fullscan = False
        self._prev_zoom = False
        self._next_paint = 0.0
        self._next_view = 0.0
        self._next_topmost = 0.0
        self._radar_hwnd: Optional[int] = None
        self._radar_window_created = False
        self._radar_zoom = False
        self._radar_last_size: Tuple[int, int] = (SQUARE_SIZE, SQUARE_SIZE)
        self._track_next = 0.0
        self._center_next = 0.0
        self._center_rmb_sent = False
        self._center_best_error = float("inf")
        self._center_progress_at = 0.0
        self._center_pause_until = 0.0
        self._center_warned = False
        self._center_keys_owned: Dict[str, Tuple[str, Any]] = {}
        self._center_keys_started = 0.0
        self._center_rest_until = 0.0

        self._thermal_qimage: Optional[QImage] = None
        self._thermal_qimage_key: Optional[Tuple[int, Tuple[int, ...]]] = None
        self._thermal_qimage_data: Optional[np.ndarray] = None

        self._lock_tracking_before = False
        self._lock_tracking_auto = False
        self._manual_lock_owned = False

        self.scanner = ScanWorker()
        self.scanner.configure(profile=get_profile(), size=SQUARE_SIZE, interval=SCAN_INTERVAL)
        self.scanner.start()

        self.thermal_worker = ThermalWorker()
        self.thermal_worker.configure(profile=get_profile())
        self.thermal_worker.start()

        if config.get("SHOW_RADAR_VIEW", True):
            self._ensure_radar_view()
            self._apply_radar_window_size()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.logic_tick)
        self.timer.start(10)


    def _radar_view_wanted(self) -> bool:
        """Окно нужно, если оно включено в конфиге или активен полный вид.

        Тепловизор в режиме overlay рисуется поверх экрана и сам по себе
        мини-окно не требует.
        """
        thermal_window = (
            shared_data.get("thermal_active") and thermal_mode() == "window"
        )
        return bool(
            config.get("SHOW_RADAR_VIEW", True)
            or shared_data.get("fullscan_active")
            or thermal_window
        )

    def _ensure_radar_view(self) -> bool:
        """Создаёт RADAR_VIEW по требованию (Q/H работают и при SHOW=false)."""
        if self.is_hidden or not self._radar_view_wanted():
            return False
        if self._radar_window_created:
            return True
        try:
            cv2.namedWindow(RADAR_WINDOW_TITLE, cv2.WINDOW_AUTOSIZE)
            self._radar_window_created = True
            self._radar_hwnd = None
            self._next_topmost = 0.0
            try:
                cv2.setMouseCallback(RADAR_WINDOW_TITLE, self._on_radar_mouse)
            except Exception:
                pass
            return True
        except Exception as exc:
            print(f"[view] create: {exc}")
            self._radar_window_created = False
            return False

    def _maybe_close_radar_view(self) -> None:
        """Закрывает окно, если оно больше не нужно для Q/H."""
        if self._radar_view_wanted():
            return
        if not self._radar_window_created:
            return
        try:
            cv2.destroyWindow(RADAR_WINDOW_TITLE)
        except Exception:
            pass
        self._radar_window_created = False
        self._radar_hwnd = None

    def toggle_interface_mode(self) -> None:
        """Переключает TV-интерфейс и обычный курсор (кнопка в RADAR_VIEW)."""
        config["INTERFACE_MODE"] = "normal" if interface_is_new() else "new"
        save_config()
        self._next_view = 0.0
        self.update()
        print(f"[view] interface {config['INTERFACE_MODE']}")

    @staticmethod
    def _ui_button_rect(width: int = SQUARE_SIZE) -> Tuple[int, int, int, int]:
        """Прямоугольник кнопки UI в координатах кадра RADAR_VIEW."""
        bw = min(104, max(48, width - 16))
        bh = 22
        return width - bw - 8, 8, bw, bh

    def _draw_ui_button(self, frame: np.ndarray) -> None:
        """Рисует кликабельную кнопку NEW/NORMAL в углу мини-окна."""
        height, width = frame.shape[:2]
        x, y, bw, bh = self._ui_button_rect(width)
        new_ui = interface_is_new()
        label = "UI: NEW" if new_ui else "UI: NORMAL"
        color = (245, 245, 245) if new_ui else (175, 175, 175)
        cv2.rectangle(frame, (x, y), (x + bw - 1, y + bh - 1), (18, 18, 18), -1)
        cv2.rectangle(frame, (x, y), (x + bw - 1, y + bh - 1), color, 1)
        font_scale = 0.38 if bw >= 88 else 0.30
        cv2.putText(
            frame, label, (x + 7, y + 15),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 1, cv2.LINE_AA,
        )

    def _on_radar_mouse(self, event, x, y, flags, param) -> None:
        """Обрабатывает клик по кнопке UI в окне RADAR_VIEW."""
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        width, height = self._radar_last_size
        if width <= 0 or height <= 0:
            return
        fx = x * SQUARE_SIZE / float(width)
        fy = y * SQUARE_SIZE / float(height)
        bx, by, bw, bh = self._ui_button_rect(SQUARE_SIZE)
        if bx <= fx < bx + bw and by <= fy < by + bh:
            self.toggle_interface_mode()

    def toggle_hide(self) -> None:
        if not self.is_hidden:
            self.is_hidden = True
            shared_data["active"] = False
            shared_data["tracking_mode"] = False
            shared_data["thermal_active"] = False
            shared_data["fullscan_active"] = False
            shared_data["targets"] = []
            shared_data["main_target"] = None
            shared_data["full_targets"] = []
            shared_data["full_main"] = None
            shared_data["zoom_hold"] = False
            shared_data["center_hold"] = False
            self._release_center_inputs()
            self.clear_manual_lock()
            self.scanner.set_active(False)
            self.thermal_worker.set_thermal(False)
            self.thermal_worker.set_fullscan(False)
            self.thermal_worker.set_zoom(False)
            if self._radar_window_created:
                try:
                    cv2.destroyWindow(RADAR_WINDOW_TITLE)
                except Exception:
                    pass
                self._radar_window_created = False
                self._radar_hwnd = None
            self.hide()
        else:
            self.is_hidden = False
            self.show()
            self._restore_radar_window()

    def _restore_radar_window(self) -> None:
        if not self._radar_view_wanted():
            return
        if not self._ensure_radar_view():
            return
        self._apply_radar_window_size()

    def toggle_radar_zoom(self) -> None:
        """Включает/выключает увеличение окна RADAR_VIEW в 2 раза."""
        self._radar_zoom = not self._radar_zoom
        self._next_view = 0.0
        self._apply_radar_window_size()
        print(f"[view] zoom x{2 if self._radar_zoom else 1}")

    def _apply_radar_window_size(self) -> None:
        """Подгоняет окно RADAR_VIEW под текущий масштаб и держит его на экране."""
        if self.is_hidden or not self._radar_view_wanted():
            return
        if not self._ensure_radar_view():
            return
        size = SQUARE_SIZE * (2 if self._radar_zoom else 1)
        try:
            cv2.resizeWindow(RADAR_WINDOW_TITLE, size, size)
            hwnd = win32gui.FindWindow(None, RADAR_WINDOW_TITLE)
            if not hwnd:
                return
            self._radar_hwnd = hwnd
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            sw = win32api.GetSystemMetrics(0)
            sh = win32api.GetSystemMetrics(1)
            x, y = left, top
            if right > sw:
                x = max(0, sw - (right - left))
            if bottom > sh:
                y = max(0, sh - (bottom - top))
            if (x, y) != (left, top):
                cv2.moveWindow(RADAR_WINDOW_TITLE, x, y)
        except Exception:
            pass


    @staticmethod
    def target_center(rect: Rect) -> Tuple[int, int]:
        x, y, w, h = rect
        return x + w // 2, y + h // 2

    def _cursor_over_radar_view(self) -> bool:
        """Не перехватываем курсор, когда пользователь работает с мини-окном."""
        if not self._radar_window_created:
            return False
        hwnd = self._radar_hwnd
        if not hwnd:
            try:
                hwnd = win32gui.FindWindow(None, RADAR_WINDOW_TITLE)
                self._radar_hwnd = hwnd
            except Exception:
                return False
        if not hwnd:
            return False
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            x, y = win32api.GetCursorPos()
            return left <= x < right and top <= y < bottom
        except Exception:
            return False

    def _roblox_is_foreground(self) -> bool:
        """Инжектить мышь можно только когда окно Roblox действительно активно."""
        try:
            fg = win32gui.GetForegroundWindow()
            if not fg:
                return False
            try:
                root = win32gui.GetAncestor(fg, 2)
                if root:
                    fg = root
            except Exception:
                pass
            hwnd = find_roblox_hwnd()
            if hwnd and (fg == hwnd or bool(win32gui.IsChild(hwnd, fg))):
                return True
            title = win32gui.GetWindowText(fg).lower()
            if title:
                for needle in THERMAL_WINDOWS:
                    if needle.lower() in title:
                        return True
        except Exception:
            pass
        return False

    def _set_center_rmb(self, down: bool) -> None:
        """Зажимает/отпускает RMB для обзора камеры на время N."""
        if down:
            if self._center_rmb_sent or not center_auto_rmb():
                return
            try:
                if win32api.GetAsyncKeyState(win32con.VK_RBUTTON) & 0x8000:
                    return
                win32api.mouse_event(
                    win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0
                )
                self._center_rmb_sent = True
            except Exception:
                pass
            return
        if not self._center_rmb_sent:
            return
        try:
            win32api.mouse_event(
                win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0
            )
        except Exception:
            pass
        self._center_rmb_sent = False

    def _press_center_key(self, name: str) -> None:
        """Зажимает клавишу коррекции, но не трогает уже зажатую пользователем."""
        raw = str(name or "").strip()
        if not raw:
            return
        ident = raw.upper()
        if ident in self._center_keys_owned:
            return
        try:
            vk = resolve_vk(raw)
        except ValueError:
            return
        try:
            if win32api.GetAsyncKeyState(vk) & 0x8000:
                return
        except Exception:
            pass

        pd_name = raw.lower()
        if pydirectinput is not None:
            try:
                mapping = getattr(pydirectinput, "KEYBOARD_MAPPING", {})
                if pd_name in mapping:
                    pydirectinput.keyDown(pd_name)
                    self._center_keys_owned[ident] = ("pd", pd_name)
                    return
            except Exception:
                pass
        try:
            win32api.keybd_event(vk, 0, 0, 0)
            self._center_keys_owned[ident] = ("vk", vk)
        except Exception:
            pass

    def _release_center_key(self, ident: str) -> None:
        spec = self._center_keys_owned.pop(ident, None)
        if spec is None:
            return
        kind, code = spec
        if kind == "pd" and pydirectinput is not None:
            try:
                pydirectinput.keyUp(str(code))
                return
            except Exception:
                pass
        try:
            try:
                vk = int(code)
            except (TypeError, ValueError):
                vk = resolve_vk(str(code))
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

    def _release_center_keys(self, reset: bool = True) -> None:
        for ident in list(self._center_keys_owned):
            self._release_center_key(ident)
        if reset:
            self._center_keys_started = 0.0

    def _sync_center_keys(self, names: List[str]) -> None:
        wanted: Dict[str, str] = {}
        for name in names:
            raw = str(name or "").strip()
            if raw:
                wanted[raw.upper()] = raw
        for ident in list(self._center_keys_owned):
            if ident not in wanted:
                self._release_center_key(ident)
        for ident, raw in wanted.items():
            if ident not in self._center_keys_owned:
                self._press_center_key(raw)

    def _release_center_inputs(self) -> None:
        self._release_center_keys()
        self._set_center_rmb(False)

    def _reset_center_progress(self, now: Optional[float] = None) -> None:
        """Новая цель/центр/потеря фокуса — начинаем прогресс заново."""
        self._center_best_error = float("inf")
        self._center_progress_at = time.perf_counter() if now is None else now

    def _input_allowed(self) -> bool:
        return self._roblox_is_foreground() and not self._cursor_over_radar_view()

    def execute_tracking(self, rect: Rect) -> None:
        now = time.perf_counter()
        if now < self._track_next:
            return
        if not self._input_allowed():
            return
        self._track_next = now + 0.04
        try:
            win32api.SetCursorPos(self.target_center(rect))
        except Exception:
            pass

    def _key_center_step(self, ex: int, ey: int, now: float) -> None:
        """Одна серия коррекции удержанием A/D/W/S, не дольше 1 секунды."""
        self._set_center_rmb(False)
        if now < self._center_rest_until:
            self._release_center_keys()
            return

        left = str(config.get("CENTER_KEY_LEFT", "A"))
        right = str(config.get("CENTER_KEY_RIGHT", "D"))
        up = str(config.get("CENTER_KEY_UP", "W"))
        down = str(config.get("CENTER_KEY_DOWN", "S"))
        wanted: List[str] = []
        if ex > CENTER_DEADZONE:
            wanted.append(right)
        elif ex < -CENTER_DEADZONE:
            wanted.append(left)
        if ey > CENTER_DEADZONE:
            wanted.append(down)
        elif ey < -CENTER_DEADZONE:
            wanted.append(up)
        if not wanted:
            self._release_center_inputs()
            self._center_rest_until = 0.0
            return

        if self._center_keys_started <= 0.0:
            self._center_keys_started = now
        hold = float(config.get("CENTER_HOLD_SECONDS", 1.0))
        rest = float(config.get("CENTER_REST_SECONDS", 0.15))
        if now - self._center_keys_started >= hold:
            self._release_center_keys()
            self._center_rest_until = now + rest
            return
        self._sync_center_keys(wanted)

    def execute_center_hold(self, rect: Rect) -> None:
        """Возвращает цель в центр экрана удержанием клавиш коррекции.

        Курсор не двигается: макрос зажимает A/D/W/S (настраивается) на одну
        серию не дольше CENTER_HOLD_SECONDS, затем отпускает и делает паузу.
        """
        now = time.perf_counter()
        if now < self._center_next:
            return
        self._center_next = now + CENTER_INTERVAL
        if not self._input_allowed():
            self._release_center_inputs()
            self._center_rest_until = 0.0
            self._reset_center_progress(now)
            return
        if now < self._center_pause_until:
            self._release_center_inputs()
            return

        cx, cy = shared_data.get("client_center", (0, 0))
        if cx <= 0 or cy <= 0:
            cx, cy = shared_data["screen_center"]
        if cx <= 0 or cy <= 0:
            self._release_center_inputs()
            self._reset_center_progress(now)
            return
        tx, ty = self.target_center(rect)
        ex, ey = tx - cx, ty - cy
        if abs(ex) <= CENTER_DEADZONE and abs(ey) <= CENTER_DEADZONE:
            self._release_center_inputs()
            self._center_rest_until = 0.0
            self._reset_center_progress(now)
            return

        error = math.hypot(ex, ey)
        if error < self._center_best_error - 1.0:
            self._center_best_error = error
            self._center_progress_at = now
        elif now - self._center_progress_at > 1.5:
            self._center_pause_until = now + 0.7
            self._center_best_error = float("inf")
            self._center_progress_at = now
            self._release_center_inputs()
            self._center_rest_until = 0.0
            if not self._center_warned:
                mode = "CENTER_KEY_*" if center_use_keys() else "RMB"
                print(
                    "[aim] center hold: цель не реагирует — "
                    f"проверьте {mode}"
                )
                self._center_warned = True
            return

        if center_use_keys():
            self._key_center_step(ex, ey, now)
            return

        self._release_center_keys()
        dx = int(round(ex * CENTER_GAIN))
        dy = int(round(ey * CENTER_GAIN))
        dx = max(-CENTER_MAX_STEP, min(CENTER_MAX_STEP, dx))
        dy = max(-CENTER_MAX_STEP, min(CENTER_MAX_STEP, dy))
        if dx == 0 and dy == 0:
            return
        self._set_center_rmb(True)
        try:
            win32api.mouse_event(
                win32con.MOUSEEVENTF_MOVE, dx, dy, 0, 0
            )
        except Exception:
            try:
                cur_x, cur_y = win32api.GetCursorPos()
                win32api.SetCursorPos((cur_x + dx, cur_y + dy))
            except Exception:
                pass

    def toggle_center_hold(self) -> None:
        shared_data["center_hold"] = not bool(shared_data["center_hold"])
        self._release_center_inputs()
        if shared_data["center_hold"]:
            self._center_best_error = float("inf")
            self._center_progress_at = time.perf_counter()
            self._center_pause_until = 0.0
            self._center_warned = False
            self._center_rest_until = 0.0
            self._center_keys_started = 0.0
        else:
            self._center_rest_until = 0.0
        state = "ON" if shared_data["center_hold"] else "OFF"
        print(f"[aim] center hold {state}")
        self.update()


    def cycle_profile(self) -> None:
        global active_profile
        names = list(config["SEARCH_PROFILES"])
        if not names:
            return
        try:
            index = (names.index(active_profile) + 1) % len(names)
        except ValueError:
            index = 0
        active_profile = names[index]
        config["ACTIVE_PROFILE"] = active_profile
        self.scanner.configure(profile=get_profile())
        self.thermal_worker.configure(profile=get_profile())
        save_config()
        print(f"[profile] {get_profile()['title']}")


    def _nearby_capture_targets(
        self,
        origin: Tuple[int, int],
        mx: int,
        my: int,
        size: int,
        current: Optional[Rect],
    ) -> List[Rect]:
        """Готовые рамки F/H рядом с курсором, ближайшие первыми."""
        rects: List[Rect] = []
        if shared_data.get("fullscan_active"):
            rects.extend(shared_data.get("full_targets", []))
        if shared_data.get("active"):
            rects.extend(shared_data.get("targets", []))

        candidates: List[Tuple[float, Rect]] = []
        for rect in rects:
            rx, ry, rw, rh = rect
            cx, cy = rx + rw // 2, ry + rh // 2
            distance = math.hypot(cx - mx, cy - my)
            if distance > CAPTURE_RADIUS:
                continue
            if current is not None:
                ccx, ccy = self.target_center(current)
                if math.hypot(cx - ccx, cy - ccy) <= max(12, size * 0.5):
                    continue
            candidates.append((distance, rect))
        candidates.sort(key=lambda item: item[0])
        return [rect for _, rect in candidates]

    @staticmethod
    def _patch_score(patch: np.ndarray) -> float:
        """Контраст + плотность краёв: цель заметно выделяется на фоне."""
        std = float(patch.std())
        edges = float(np.abs(cv2.Laplacian(patch, cv2.CV_32F)).mean())
        return std + edges * 1.5

    def _find_texture_patch(
        self,
        gray: np.ndarray,
        origin: Tuple[int, int],
        mx: int,
        my: int,
        size: int,
        current: Optional[Rect],
    ) -> Optional[Tuple[np.ndarray, Tuple[float, float], Rect]]:
        """Ищет самый выразительный участок в радиусе ~70 px от курсора."""
        h, w = gray.shape
        if size > w or size > h:
            return None
        client_cx = mx - origin[0]
        client_cy = my - origin[1]

        def patch_at(center_x: float, center_y: float) -> Optional[Tuple[np.ndarray, int, int]]:
            left = int(round(center_x - size / 2.0))
            top = int(round(center_y - size / 2.0))
            if left < 0 or top < 0 or left + size > w or top + size > h:
                return None
            return gray[top:top + size, left:left + size].copy(), left, top

        exact = patch_at(client_cx, client_cy)
        exact_score = self._patch_score(exact[0]) if exact is not None else -1.0
        if exact is not None and float(exact[0].std()) >= 4.0:
            left, top = exact[1], exact[2]
            return exact[0], (left + size / 2.0, top + size / 2.0), (
                origin[0] + left, origin[1] + top, size, size
            )

        best: Optional[Tuple[float, np.ndarray, int, int]] = None
        radius = 72
        step = 8
        for dy in range(-radius, radius + 1, step):
            for dx in range(-radius, radius + 1, step):
                found = patch_at(client_cx + dx, client_cy + dy)
                if found is None:
                    continue
                patch, left, top = found
                if float(patch.std()) < 2.0:
                    continue
                distance = math.hypot(dx, dy)
                score = self._patch_score(patch) - distance * 0.03
                if best is None or score > best[0]:
                    best = (score, patch, left, top)

        if best is not None and best[1].std() >= 2.0:
            _, patch, left, top = best
            center = (left + size / 2.0, top + size / 2.0)
            screen_rect = (origin[0] + left, origin[1] + top, size, size)
            if current is not None:
                ccx, ccy = self.target_center(current)
                if math.hypot(
                    origin[0] + center[0] - ccx,
                    origin[1] + center[1] - ccy,
                ) <= max(12, size * 0.5):
                    return None
            return patch, center, screen_rect

        if exact is not None and exact_score >= 0.0 and float(exact[0].std()) >= 2.0:
            left, top = exact[1], exact[2]
            return exact[0], (left + size / 2.0, top + size / 2.0), (
                origin[0] + left, origin[1] + top, size, size
            )
        return None

    def toggle_manual_lock(self) -> None:
        """Захват/смена цели рядом с курсором и хардлок по внешнему виду.

        Сначала используются готовые рамки F/H, затем локальный поиск по
        контрасту вокруг курсора. Повторное T на текущей цели снимает LOCK;
        T на другой цели переключает хардлок без промежуточного снятия.
        """
        captured = capture_roblox_window()
        if captured is None:
            print("[lock] окно Roblox не найдено")
            return

        gray, origin = captured
        size = lock_template_size()
        mx, my = win32api.GetCursorPos()
        h, w = gray.shape
        if not (
            origin[0] <= mx < origin[0] + w
            and origin[1] <= my < origin[1] + h
        ):
            print("[lock] курсор вне окна Roblox")
            return

        current = self.scanner.manual_lock_rect() if self._manual_lock_owned else None
        if current is not None:
            ccx, ccy = self.target_center(current)
            near_current = math.hypot(mx - ccx, my - ccy) <= max(
                24, size * 0.75
            )
            if near_current:
                other = self._nearby_capture_targets(origin, mx, my, size, current)
                if not other:
                    self._finish_manual_lock("released")
                    return

        candidates = self._nearby_capture_targets(origin, mx, my, size, current)
        chosen: Optional[Tuple[np.ndarray, Tuple[float, float], Rect]] = None
        if candidates:
            rect = candidates[0]
            rx, ry, rw, rh = rect
            center_x = (rx + rw / 2.0) - origin[0]
            center_y = (ry + rh / 2.0) - origin[1]
            left = int(round(center_x - size / 2.0))
            top = int(round(center_y - size / 2.0))
            if left >= 0 and top >= 0 and left + size <= w and top + size <= h:
                patch = gray[top:top + size, left:left + size].copy()
                if float(patch.std()) >= 2.0:
                    chosen = (
                        patch,
                        (left + size / 2.0, top + size / 2.0),
                        (origin[0] + left, origin[1] + top, size, size),
                    )
        if chosen is None:
            chosen = self._find_texture_patch(
                gray, origin, mx, my, size, current
            )
        if chosen is None:
            print("[lock] цель рядом с курсором не найдена")
            return

        patch, center, screen_rect = chosen
        if current is not None:
            ccx, ccy = self.target_center(current)
            ncx, ncy = self.target_center(screen_rect)
            if math.hypot(ncx - ccx, ncy - ccy) <= max(12, size * 0.5):
                print("[lock] already locked")
                return

        switching = current is not None
        if not switching:
            self._lock_tracking_before = bool(shared_data["tracking_mode"])
            self._lock_tracking_auto = False
            if keys.pressed(VK_SCAN) and not self._lock_tracking_before:
                shared_data["tracking_mode"] = True
                self._lock_tracking_auto = True
        self._manual_lock_owned = True
        self.scanner.set_manual_lock(patch, center, screen_rect)
        shared_data["lock_target"] = screen_rect
        self.update()
        print(
            f"[lock] {'switched' if switching else 'captured'} "
            f"({size}x{size}, {lock_mode_title()})"
        )

    def _resize_manual_lock(self, size: int) -> bool:
        """Пересоздаёт шаблон LOCK вокруг текущей цели с новым размером."""
        current = self.scanner.manual_lock_rect()
        if current is None:
            return False
        captured = capture_roblox_window()
        if captured is None:
            print("[lock] окно Roblox не найдено для смены размера")
            return False
        gray, origin = captured
        h, w = gray.shape
        if size > w or size > h:
            print(f"[lock] размер {size} больше окна Roblox")
            return False
        cx, cy = self.target_center(current)
        left = int(round(cx - origin[0] - size / 2.0))
        top = int(round(cy - origin[1] - size / 2.0))
        left = max(0, min(w - size, left))
        top = max(0, min(h - size, top))
        patch = gray[top:top + size, left:left + size].copy()
        if float(patch.std()) < 2.0:
            print("[lock] не удалось переснять цель для нового размера")
            return False
        center = (left + size / 2.0, top + size / 2.0)
        rect = (origin[0] + left, origin[1] + top, size, size)
        self.scanner.set_manual_lock(patch, center, rect)
        shared_data["lock_target"] = rect
        return True

    def cycle_lock_size_mode(self) -> None:
        """X: normal -> far -> near. Меняет размер шаблона и рамки LOCK."""
        previous = lock_size_mode()
        index = LOCK_SIZE_MODES.index(previous)
        mode = LOCK_SIZE_MODES[(index + 1) % len(LOCK_SIZE_MODES)]
        size = lock_template_size(mode)

        if self._manual_lock_owned and self.scanner.manual_lock_rect() is not None:
            if not self._resize_manual_lock(size):
                self.update()
                print(f"[lock] size mode: {previous.upper()} (не изменён)")
                return

        config["LOCK_SIZE_MODE"] = mode
        save_config()
        self.update()
        print(f"[lock] size mode: {mode.upper()} ({size}x{size})")

    def _finish_manual_lock(self, reason: str) -> None:
        """Снимает захват; выключает V, только если захват сам его включил."""
        auto_enabled = self._lock_tracking_auto
        self.clear_manual_lock()
        if auto_enabled:
            shared_data["tracking_mode"] = False
        print(f"[lock] {reason}")

    def clear_manual_lock(self) -> None:
        self.scanner.clear_manual_lock()
        shared_data["lock_target"] = None
        self._lock_tracking_before = False
        self._lock_tracking_auto = False
        self._manual_lock_owned = False
        self.update()


    def toggle_thermal(self) -> None:
        if not shared_data["thermal_active"]:
            shared_data["thermal_active"] = True
            self.thermal_worker.set_thermal(True)
            self._ensure_radar_view()
            self._apply_radar_window_size()
            self._next_view = 0.0
            self.raise_()
            where = "OVERLAY" if thermal_mode() == "overlay" else "RADAR_VIEW"
            print(f"[thermal] ON ({where})")
        else:
            shared_data["thermal_active"] = False
            self.thermal_worker.set_thermal(False)
            self._maybe_close_radar_view()
            save_config()
        self.update()

    def toggle_thermal_mode(self) -> None:
        """Переключает показ тепловизора: мини-окно <-> полноэкранный оверлей."""
        new_mode = "overlay" if thermal_mode() == "window" else "window"
        config["THERMAL_MODE"] = new_mode
        save_config()

        if shared_data["thermal_active"]:
            if new_mode == "window":
                self._ensure_radar_view()
                self._apply_radar_window_size()
            else:
                self._maybe_close_radar_view()
        self._next_view = 0.0
        self.update()
        title = "OVERLAY" if new_mode == "overlay" else "RADAR_VIEW"
        print(f"[thermal] mode: {title}")

    def toggle_fullscan(self) -> None:
        """Полноэкранный поиск целей по активному профилю, независимо от Q."""
        if not shared_data["fullscan_active"]:
            shared_data["fullscan_active"] = True
            self.thermal_worker.set_fullscan(True)
            self._ensure_radar_view()
            self._apply_radar_window_size()
            self._next_view = 0.0
            print("[fullscan] ON")
        else:
            shared_data["fullscan_active"] = False
            self.thermal_worker.set_fullscan(False)
            shared_data["full_targets"] = []
            shared_data["full_main"] = None
            self._maybe_close_radar_view()
            print("[fullscan] OFF")
        self.update()

    def _handle_thermal_hotkeys(self) -> None:
        """Опрашивает Num-клавиши каждый тик, применяет только при активном thermal.

        Опрос без пропусков нужен, чтобы _prev в KeyState не устаревал.
        """
        active = shared_data["thermal_active"]
        settings = THERMAL_SETTINGS
        threshold_up = keys.just_pressed(win32con.VK_ADD)
        threshold_down = keys.just_pressed(win32con.VK_SUBTRACT)
        dot_up = keys.just_pressed(win32con.VK_NUMPAD8)
        dot_down = keys.just_pressed(win32con.VK_NUMPAD2)
        step_down = keys.just_pressed(win32con.VK_NUMPAD4)
        step_up = keys.just_pressed(win32con.VK_NUMPAD6)
        bg_down = keys.just_pressed(win32con.VK_NUMPAD7)
        bg_up = keys.just_pressed(win32con.VK_NUMPAD9)
        if not active:
            return
        if threshold_up:
            settings["threshold"] = min(80, settings["threshold"] + 1)
        if threshold_down:
            settings["threshold"] = max(1, settings["threshold"] - 1)
        if dot_up:
            settings["dot_size"] = min(8, settings["dot_size"] + 1)
        if dot_down:
            settings["dot_size"] = max(1, settings["dot_size"] - 1)
        if step_down:
            settings["sample_step"] = max(1, settings["sample_step"] - 1)
        if step_up:
            settings["sample_step"] = min(12, settings["sample_step"] + 1)
        if bg_down:
            settings["bg_alpha"] = max(0, settings["bg_alpha"] - 10)
        if bg_up:
            settings["bg_alpha"] = min(255, settings["bg_alpha"] + 10)


    def _poll_hotkeys(self) -> None:
        if keys.just_pressed(VK_EXIT):
            self.shutdown()
            return
        if keys.just_pressed(VK_HIDE):
            self.toggle_hide()
            return
        if self.is_hidden:
            return

        if keys.just_pressed(VK_TRACK):
            shared_data["tracking_mode"] = not shared_data["tracking_mode"]
        if keys.just_pressed(VK_THERMAL):
            self.toggle_thermal()
        if keys.just_pressed(VK_FULLSCAN):
            self.toggle_fullscan()
        if keys.just_pressed(VK_THERMAL_MODE):
            self.toggle_thermal_mode()
        if keys.just_pressed(VK_PROFILE):
            self.cycle_profile()
        if keys.just_pressed(VK_ZOOM):
            self.toggle_radar_zoom()
        if keys.just_pressed(VK_CAPTURE):
            self.toggle_manual_lock()
        if keys.just_pressed(VK_LOCK_MODE):
            self.cycle_lock_size_mode()
        if keys.just_pressed(VK_CENTER_HOLD):
            self.toggle_center_hold()
        self._handle_thermal_hotkeys()


    def logic_tick(self) -> None:
        if self._closing:
            return
        self._poll_hotkeys()
        if self._closing or self.is_hidden:
            return

        active = keys.pressed(VK_SCAN)
        full_active = bool(shared_data["fullscan_active"])
        zoom_hold = keys.pressed(VK_ZOOM_HOLD)
        if self._center_rmb_sent and VK_ZOOM_HOLD == win32con.VK_RBUTTON:
            zoom_hold = False
        thermal_overlay = bool(
            shared_data["thermal_active"] and thermal_mode() == "overlay"
        )
        state_changed = active != self._prev_active
        full_state_changed = full_active != self._prev_fullscan
        zoom_state_changed = zoom_hold != self._prev_zoom
        if state_changed or full_state_changed or zoom_state_changed:
            self._next_view = 0.0
        shared_data["active"] = active
        shared_data["zoom_hold"] = zoom_hold
        self.scanner.set_active(active)
        self.thermal_worker.set_zoom(zoom_hold)

        full_snap = self.thermal_worker.snapshot() if (
            shared_data["thermal_active"] or full_active or zoom_hold
        ) else None
        if full_snap is not None and full_snap.size[0] > 0:
            shared_data["client_center"] = (
                full_snap.origin[0] + full_snap.size[0] // 2,
                full_snap.origin[1] + full_snap.size[1] // 2,
            )
        if full_active and full_snap is not None:
            shared_data["full_targets"] = list(full_snap.targets)
            shared_data["full_main"] = full_snap.main
        else:
            shared_data["full_targets"] = []
            shared_data["full_main"] = None
        full_main = shared_data["full_main"]
        center_target = None

        if active:
            targets, main, motion, lock = self.scanner.snapshot()
            if self._manual_lock_owned and not self.scanner.has_manual_lock():
                self._finish_manual_lock("lost")
                lock = None
            shared_data["targets"] = targets
            shared_data["main_target"] = main
            shared_data["lock_target"] = lock
            steer = lock if lock is not None else (main if main is not None else full_main)
            if steer is not None:
                if shared_data["center_hold"]:
                    center_target = steer
                    self.execute_center_hold(steer)
                elif shared_data["tracking_mode"]:
                    self.execute_tracking(steer)
        else:
            if self._manual_lock_owned and not self.scanner.has_manual_lock():
                self._finish_manual_lock("lost")
            targets, main, motion = [], None, None
            shared_data["targets"] = []
            shared_data["main_target"] = None
            shared_data["lock_target"] = (
                self.scanner.manual_lock_rect() if self._manual_lock_owned else None
            )
            steer = shared_data["lock_target"]
            if steer is None and full_active:
                steer = full_main
            if steer is not None:
                if shared_data["center_hold"]:
                    center_target = steer
                    self.execute_center_hold(steer)
                elif shared_data["tracking_mode"]:
                    self.execute_tracking(steer)

        if center_target is None:
            self._release_center_inputs()
            self._center_rest_until = 0.0
            self._reset_center_progress()

        self._update_radar_view(targets, motion, active, full_snap)

        now = time.perf_counter()
        if state_changed or full_state_changed or zoom_state_changed or (
            (
                active
                or self._prev_active
                or self._manual_lock_owned
                or full_active
                or self._prev_fullscan
                or thermal_overlay
                or zoom_hold
                or self._prev_zoom
                or shared_data["center_hold"]
            )
            and now >= self._next_paint
        ):
            self._next_paint = now + 0.033
            self.update()
        self._prev_active = active
        self._prev_fullscan = full_active
        self._prev_zoom = zoom_hold


    def _update_radar_view(
        self,
        targets: List[Rect],
        motion: Optional[np.ndarray],
        active: bool,
        full_snap: Optional[FullViewSnapshot],
    ) -> None:
        if not self._ensure_radar_view():
            return
        now = time.perf_counter()
        if now < self._next_view:
            return
        self._next_view = now + 0.05

        thermal_window = bool(
            shared_data.get("thermal_active") and thermal_mode() == "window"
        )
        full_view = bool(shared_data.get("fullscan_active") or thermal_window)
        if full_view:
            if thermal_mode() == "overlay" and full_snap is not None:
                full_snap = full_snap._replace(thermal=None)
            frame = self._render_full_view(full_snap)
        else:
            frame = np.zeros((SQUARE_SIZE, SQUARE_SIZE, 3), dtype=np.uint8)
            zoom_hold = bool(shared_data.get("zoom_hold"))
            if motion is not None:
                gain = int(config.get("MOTION_MAP_GAIN", 8))
                scaled = motion if gain == 1 else cv2.convertScaleAbs(motion, alpha=gain)
                if zoom_hold:
                    h, w = scaled.shape[:2]
                    ch, cw = max(8, h // 2), max(8, w // 2)
                    scaled = scaled[
                        (h - ch) // 2:(h - ch) // 2 + ch,
                        (w - cw) // 2:(w - cw) // 2 + cw,
                    ]
                if scaled.shape[:2] == (SQUARE_SIZE, SQUARE_SIZE):
                    frame = cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)
                else:
                    frame = cv2.cvtColor(
                        cv2.resize(scaled, (SQUARE_SIZE, SQUARE_SIZE)),
                        cv2.COLOR_GRAY2BGR,
                    )

            self._draw_radar_status(frame, active, len(targets), self._radar_zoom)
            lock = shared_data.get("lock_target")
            if lock is not None:
                mx, my = win32api.GetCursorPos()
                half = SQUARE_SIZE // 2
                lx, ly, lw, lh = lock
                x1, y1 = lx - (mx - half), ly - (my - half)
                if zoom_hold:
                    x1 = x1 * 2 - half
                    y1 = y1 * 2 - half
                    lw *= 2
                    lh *= 2
                if x1 + lw > 0 and y1 + lh > 0 and x1 < SQUARE_SIZE and y1 < SQUARE_SIZE:
                    cv2.rectangle(frame, (x1, y1), (x1 + lw, y1 + lh), (245, 245, 245), 1)
                    label_y = y1 - 4 if y1 >= 14 else y1 + lh + 12
                    cv2.putText(
                        frame, "LOCK", (max(2, x1), min(SQUARE_SIZE - 4, label_y)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (245, 245, 245), 1,
                    )

        self._draw_ui_button(frame)

        if self._radar_zoom:
            frame = cv2.resize(
                frame, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST
            )
        self._radar_last_size = (frame.shape[1], frame.shape[0])
        try:
            cv2.imshow(RADAR_WINDOW_TITLE, frame)
            cv2.waitKey(1)
        except Exception as exc:
            print(f"[view] {exc}")
            self._radar_hwnd = None
            self._radar_window_created = False
            return

        stale_hwnd = self._radar_hwnd is None or not win32gui.IsWindow(self._radar_hwnd)
        if stale_hwnd or now >= self._next_topmost:
            hwnd = win32gui.FindWindow(None, RADAR_WINDOW_TITLE)
            if hwnd:
                self._radar_hwnd = hwnd
                try:
                    win32gui.SetWindowPos(
                        hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
                    )
                except Exception:
                    pass
            self._next_topmost = now + 1.0

    @staticmethod
    def _full_layout() -> Tuple[int, int]:
        """Высота верхней панели статуса и нижней подсказки."""
        top = max(16, int(SQUARE_SIZE * 0.25))
        bottom = max(10, int(SQUARE_SIZE * 0.08))
        if top + bottom > SQUARE_SIZE - 32:
            top = max(16, (SQUARE_SIZE - 32) // 2)
            bottom = max(10, SQUARE_SIZE - 32 - top)
        return top, bottom

    @classmethod
    def _full_geometry(cls, full_size: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
        """Вписывает клиент Roblox в свободную область RADAR_VIEW."""
        full_w, full_h = full_size
        if full_w <= 0 or full_h <= 0:
            return None
        top, bottom = cls._full_layout()
        available_w = SQUARE_SIZE
        available_h = max(1, SQUARE_SIZE - top - bottom)
        scale = min(available_w / float(full_w), available_h / float(full_h))
        width = max(1, int(round(full_w * scale)))
        height = max(1, int(round(full_h * scale)))
        x0 = (SQUARE_SIZE - width) // 2
        y0 = top + (available_h - height) // 2
        return x0, y0, width, height

    @staticmethod
    def _map_full_rect(
        rect: Rect,
        origin: Tuple[int, int],
        full_size: Tuple[int, int],
        geometry: Tuple[int, int, int, int],
    ) -> Optional[Rect]:
        """Экранный прямоугольник -> координаты полного вида RADAR_VIEW."""
        full_w, full_h = full_size
        if full_w <= 0 or full_h <= 0:
            return None
        x0, y0, width, height = geometry
        rx, ry, rw, rh = rect
        left = x0 + (rx - origin[0]) * width / float(full_w)
        top = y0 + (ry - origin[1]) * height / float(full_h)
        right = left + rw * width / float(full_w)
        bottom = top + rh * height / float(full_h)

        if right <= x0 or left >= x0 + width or bottom <= y0 or top >= y0 + height:
            return None
        x1 = int(round(max(x0, left)))
        y1 = int(round(max(y0, top)))
        x2 = int(round(min(x0 + width - 1, right)))
        y2 = int(round(min(y0 + height - 1, bottom)))
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2 - x1, y2 - y1

    def _render_full_view(self, snap: Optional[FullViewSnapshot]) -> np.ndarray:
        """Полное окно Roblox в RADAR_VIEW: тепло/motion + TV-прицел."""
        frame = np.zeros((SQUARE_SIZE, SQUARE_SIZE, 3), dtype=np.uint8)
        if snap is None:
            snap = FullViewSnapshot(
                None, None, None, None, [], None,
                (0, 0), (0, 0), False, 0,
            )

        geometry = self._full_geometry(snap.size)
        if geometry is not None:
            x0, y0, width, height = geometry
            source: Optional[np.ndarray] = None
            if snap.thermal is not None:
                source = snap.thermal
            elif snap.motion is not None:
                gain = int(config.get("MOTION_MAP_GAIN", 8))
                scaled = (
                    snap.motion
                    if gain == 1
                    else cv2.convertScaleAbs(snap.motion, alpha=gain)
                )
                source = cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)

            mx, my = win32api.GetCursorPos()
            locked = shared_data.get("lock_target") is not None
            if locked:
                ax, ay = self.target_center(shared_data["lock_target"])
            else:
                ax, ay = mx, my
            anchor_client_x = ax - snap.origin[0]
            anchor_client_y = ay - snap.origin[1]
            anchor_inside = (
                0 <= anchor_client_x < snap.size[0]
                and 0 <= anchor_client_y < snap.size[1]
            )

            crop_w = crop_h = 0
            crop_x0 = crop_y0 = 0
            if source is not None:
                zoom_hold = bool(shared_data.get("zoom_hold"))
                if zoom_hold and snap.size[0] > 0 and snap.size[1] > 0:
                    crop_w = max(32, int(round(source.shape[1] / ZOOM_HOLD_FACTOR)))
                    crop_h = max(32, int(round(source.shape[0] / ZOOM_HOLD_FACTOR)))
                    crop_w = min(crop_w, source.shape[1])
                    crop_h = min(crop_h, source.shape[0])
                    crop_x0 = int(round(max(
                        0, min(source.shape[1] - crop_w,
                               anchor_client_x - crop_w / 2.0)
                    )))
                    crop_y0 = int(round(max(
                        0, min(source.shape[0] - crop_h,
                               anchor_client_y - crop_h / 2.0)
                    )))
                    source = source[
                        crop_y0:crop_y0 + crop_h, crop_x0:crop_x0 + crop_w
                    ]
                interpolation = (
                    cv2.INTER_AREA if source.shape[1] > width else cv2.INTER_NEAREST
                )
                resized = cv2.resize(source, (width, height), interpolation=interpolation)
                frame[y0:y0 + height, x0:x0 + width] = resized

                if crop_w > 0 and crop_h > 0:
                    reticle_x = x0 + int(round(
                        (anchor_client_x - crop_x0) * width / float(crop_w)
                    ))
                    reticle_y = y0 + int(round(
                        (anchor_client_y - crop_y0) * height / float(crop_h)
                    ))
                else:
                    reticle_x = x0 + int(round(
                        anchor_client_x * width / float(snap.size[0])
                    ))
                    reticle_y = y0 + int(round(
                        anchor_client_y * height / float(snap.size[1])
                    ))
            else:
                reticle_x, reticle_y = x0 + width // 2, y0 + height // 2

            cv2.rectangle(
                frame, (x0, y0), (x0 + width - 1, y0 + height - 1), (80, 80, 80), 1
            )

            def map_rect(rect: Rect) -> Optional[Rect]:
                """Рамка в координатах кадра с учётом crop для RMB-прицела."""
                if crop_w > 0 and crop_h > 0:
                    rx, ry, rw, rh = rect
                    mx = rx - snap.origin[0] - crop_x0
                    my = ry - snap.origin[1] - crop_y0
                    return (
                        x0 + int(round(mx * width / float(crop_w))),
                        y0 + int(round(my * height / float(crop_h))),
                        max(1, int(round(rw * width / float(crop_w)))),
                        max(1, int(round(rh * height / float(crop_h)))),
                    )
                return self._map_full_rect(
                    rect, snap.origin, snap.size, geometry
                )

            for rect in snap.targets:
                mapped = map_rect(rect)
                if mapped is None:
                    continue
                tx, ty, tw, th = mapped
                is_main = (not locked) and rect == snap.main
                color = (0, 255, 0) if is_main else (0, 175, 90)
                cv2.rectangle(
                    frame, (tx, ty), (tx + tw, ty + th), color, 2 if is_main else 1
                )
                if is_main:
                    start = (
                        (reticle_x, reticle_y)
                        if anchor_inside
                        else (x0 + width // 2, y0 + height // 2)
                    )
                    cv2.line(
                        frame, start, (tx + tw // 2, ty + th // 2),
                        (0, 150, 70), 1,
                    )
                    label_y = ty - 4 if ty >= 14 else ty + th + 12
                    cv2.putText(
                        frame, "FULL", (max(2, tx), min(SQUARE_SIZE - 4, label_y)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1,
                    )

            lock = shared_data.get("lock_target")
            if lock is not None:
                mapped = map_rect(lock)
                if mapped is not None:
                    lx, ly, lw, lh = mapped
                    cv2.rectangle(
                        frame, (lx, ly), (lx + lw, ly + lh), (245, 245, 245), 2
                    )
                    label_y = ly - 4 if ly >= 14 else ly + lh + 12
                    cv2.putText(
                        frame, "LOCK", (max(2, lx), min(SQUARE_SIZE - 4, label_y)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (245, 245, 245), 1,
                    )

            if anchor_inside:
                if interface_is_new():
                    self._draw_cv_reticle(
                        frame, reticle_x, reticle_y,
                        max(130, min(width, height) // 2), locked,
                    )
                else:
                    self._draw_cv_cross(
                        frame, reticle_x, reticle_y,
                        max(130, min(width, height) // 2), locked,
                    )

        self._draw_full_status(frame, snap)
        return frame

    def _draw_full_status(
        self, frame: np.ndarray, snap: FullViewSnapshot
    ) -> None:
        """Короткая сводка в верхней панели полного вида."""
        height, width = frame.shape[:2]
        top, bottom = self._full_layout()
        panel = frame.copy()
        cv2.rectangle(panel, (0, 0), (width, top), (0, 0, 0), -1)
        cv2.addWeighted(panel, 0.72, frame, 0.28, 0, frame)

        thermal = bool(shared_data.get("thermal_active"))
        full = bool(shared_data.get("fullscan_active"))
        if thermal and full:
            mode = "THERMAL + FULL"
        elif thermal:
            mode = "THERMAL"
        elif full:
            mode = "FULL SCAN"
        else:
            mode = "IDLE"

        font = cv2.FONT_HERSHEY_SIMPLEX
        lock_on = shared_data.get("lock_target") is not None
        scan_on = bool(shared_data.get("active"))
        zoom_hold = bool(shared_data.get("zoom_hold"))
        center_hold = bool(shared_data.get("center_hold"))
        lines = [
            (
                f"PROFILE [{CV2_BIND_LABELS['profile']}]: {active_profile.upper()}",
                (0, 220, 255),
            ),
            (
                f"VIEW: {mode}  ({snap.size[0]}x{snap.size[1]})",
                (255, 255, 255),
            ),
            (
                f"FULL [{CV2_BIND_LABELS['fullscan']}]: {'ON' if full else 'OFF'}   "
                f"THERMAL [{CV2_BIND_LABELS['thermal']}]: "
                f"{'ON' if thermal else 'OFF'} "
                f"{'OVR' if thermal_mode() == 'overlay' else 'WIN'}",
                (0, 255, 0) if (full or thermal) else (150, 150, 150),
            ),
            (
                f"TARGETS: {len(snap.targets)}   "
                f"SCAN [{CV2_BIND_LABELS['scan']}]: {'ON' if scan_on else 'HOLD'}",
                (0, 255, 0) if snap.targets else (160, 160, 160),
            ),
            (
                f"LOCK [{CV2_BIND_LABELS['capture']}]: {'ON' if lock_on else 'OFF'}   "
                f"SIZE [{CV2_BIND_LABELS['lock_mode']}]: "
                f"{lock_mode_title()} {lock_template_size()}",
                (245, 245, 245) if lock_on else (150, 150, 150),
            ),
            (
                f"ZOOM [{CV2_BIND_LABELS['zoom']}]: x{2 if self._radar_zoom else 1}   "
                f"LENS [{CV2_BIND_LABELS['zoom_hold']}]: "
                f"{'ON' if zoom_hold else 'OFF'}",
                (0, 255, 0) if zoom_hold else (150, 150, 150),
            ),
            (
                f"CENTER [{CV2_BIND_LABELS['center_hold']}]: "
                f"{'ON' if center_hold else 'OFF'}",
                (0, 255, 0) if center_hold else (150, 150, 150),
            ),
        ]
        spacing = max(10, min(16, (top - 8) // max(1, len(lines) + 1)))
        font_scale = 0.35 if top >= 80 else 0.30
        y = spacing + 4
        for text, color in lines:
            cv2.putText(frame, text, (10, y), font, font_scale, color, 1)
            y += spacing

        status_y = min(top - 4, y + 2)
        if not snap.found:
            cv2.putText(
                frame, "ROBLOX WINDOW NOT FOUND", (10, status_y), font,
                font_scale, (0, 80, 255), 1,
            )
        elif snap.thermal is None and snap.motion is None:
            cv2.putText(
                frame, "WAITING FOR FRAME", (10, status_y), font,
                font_scale, (180, 180, 180), 1,
            )

        hint_panel = frame.copy()
        cv2.rectangle(hint_panel, (0, height - bottom), (width, height), (0, 0, 0), -1)
        cv2.addWeighted(hint_panel, 0.72, frame, 0.28, 0, frame)
        if bottom >= 26:
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['scan']}] scan  [{CV2_BIND_LABELS['tracking']}] track  "
                f"[{CV2_BIND_LABELS['thermal']}] thermal  [{CV2_BIND_LABELS['fullscan']}] full  "
                f"[{CV2_BIND_LABELS['capture']}] lock  [{CV2_BIND_LABELS['center_hold']}] center",
                (10, height - bottom + 12), font, 0.29, (190, 190, 190), 1,
            )
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['thermal_mode']}] mode  "
                f"[{CV2_BIND_LABELS['lock_mode']}] size  "
                f"[{CV2_BIND_LABELS['zoom']}] zoom  "
                f"[{CV2_BIND_LABELS['zoom_hold']}] lens  "
                f"[{CV2_BIND_LABELS['hide']}] hide  [ESC] exit",
                (10, height - 5), font, 0.29, (190, 190, 190), 1,
            )
        else:
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['scan']}] scan [{CV2_BIND_LABELS['thermal']}] thermal "
                f"[{CV2_BIND_LABELS['fullscan']}] full [{CV2_BIND_LABELS['capture']}] lock "
                f"[{CV2_BIND_LABELS['zoom_hold']}] TV",
                (10, height - 4), font, 0.26, (190, 190, 190), 1,
            )

    @staticmethod
    def _draw_cv_reticle(
        frame: np.ndarray, x: int, y: int, size: int, locked: bool = False
    ) -> None:
        """TV-прицел для OpenCV-окна: крест, квадрат и углы."""
        color = (245, 245, 245) if locked else (225, 225, 225)
        half = max(18, size // 2)
        corner = max(8, size // 10)
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            px, py = x + sx * half, y + sy * half
            cv2.line(frame, (px, py), (px - sx * corner, py), color, 1)
            cv2.line(frame, (px, py), (px, py - sy * corner), color, 1)
        ih = max(10, half // 2)
        ic = max(5, ih // 4)
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            px, py = x + sx * ih, y + sy * ih
            cv2.line(frame, (px, py), (px - sx * ic, py), color, 1)
            cv2.line(frame, (px, py), (px, py - sy * ic), color, 1)
        gap = 6
        cv2.line(frame, (x - half, y), (x - gap, y), color, 1)
        cv2.line(frame, (x + gap, y), (x + half, y), color, 1)
        cv2.line(frame, (x, y - half), (x, y - gap), color, 1)
        cv2.line(frame, (x, y + gap), (x, y + half), color, 1)
        box = max(8, size // 16)
        cv2.rectangle(
            frame, (x - box // 2, y - box // 2),
            (x + box // 2, y + box // 2), color, 1,
        )

    @staticmethod
    def _draw_cv_cross(
        frame: np.ndarray, x: int, y: int, size: int, locked: bool = False
    ) -> None:
        """Простой прицел для обычного (normal) интерфейса."""
        color = (245, 245, 245) if locked else (220, 220, 220)
        arm = max(8, size // 10)
        gap = 4
        cv2.line(frame, (x - arm, y), (x - gap, y), color, 1)
        cv2.line(frame, (x + gap, y), (x + arm, y), color, 1)
        cv2.line(frame, (x, y - arm), (x, y - gap), color, 1)
        cv2.line(frame, (x, y + gap), (x, y + arm), color, 1)
        cv2.circle(frame, (x, y), 2, color, -1)

    @staticmethod
    def _draw_radar_status(
        frame: np.ndarray, active: bool, targets_count: int, zoomed: bool = False
    ) -> None:
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        cx, cy = w // 2, h // 2
        cv2.line(frame, (0, cy), (w, cy), (45, 45, 45), 1)
        cv2.line(frame, (cx, 0), (cx, h), (45, 45, 45), 1)
        locked = shared_data.get("lock_target") is not None
        if interface_is_new():
            Overlay._draw_cv_reticle(
                frame, cx, cy, max(120, SQUARE_SIZE // 2), locked
            )
        else:
            Overlay._draw_cv_cross(
                frame, cx, cy, max(120, SQUARE_SIZE // 2), locked
            )
            cv2.circle(frame, (cx, cy), 2, (0, 180, 0), -1)

        cv2.putText(
            frame, f"PROFILE [{CV2_BIND_LABELS['profile']}]: {active_profile.upper()}",
            (10, 20), font, 0.45, (0, 220, 255), 1,
        )
        cv2.putText(
            frame,
            f"MOTION MAP x{int(config.get('MOTION_MAP_GAIN', 8))} (raw, before masks)",
            (10, 38), font, 0.4, (180, 180, 180), 1,
        )
        zoom_color = (0, 255, 0) if zoomed else (128, 128, 128)
        cv2.putText(
            frame,
            f"ZOOM x{2 if zoomed else 1} [{CV2_BIND_LABELS['zoom']}]: "
            f"{'ON' if zoomed else 'OFF'}",
            (10, 56), font, 0.4, zoom_color, 1,
        )
        rm_zoom = bool(shared_data.get("zoom_hold"))
        rm_color = (0, 255, 0) if rm_zoom else (128, 128, 128)
        cv2.putText(
            frame,
            f"LENS x{ZOOM_HOLD_FACTOR:.0f} [{CV2_BIND_LABELS['zoom_hold']}]: "
            f"{'ON' if rm_zoom else 'OFF'}",
            (10, 74), font, 0.4, rm_color, 1,
        )
        lock_color = (245, 245, 245) if shared_data.get("lock_target") else (128, 128, 128)
        cv2.putText(
            frame,
            f"LOCK [{CV2_BIND_LABELS['capture']}]: "
            f"{'ON' if shared_data.get('lock_target') else 'OFF'}   "
            f"SIZE [{CV2_BIND_LABELS['lock_mode']}]: "
            f"{lock_mode_title()} {lock_template_size()}",
            (10, 92), font, 0.4, lock_color, 1,
        )

        if not active:
            cv2.putText(
                frame, f"HOLD {CV2_BIND_LABELS['scan']} TO SCAN",
                (10, cy - 14), font, 0.55, (255, 255, 255), 1,
            )
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['tracking']}] track  "
                f"[{CV2_BIND_LABELS['thermal']}] thermal  "
                f"[{CV2_BIND_LABELS['fullscan']}] full  "
                f"[{CV2_BIND_LABELS['thermal_mode']}] mode",
                (10, h - 24), font, 0.32, (128, 128, 128), 1,
            )
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['zoom']}] zoom  "
                f"[{CV2_BIND_LABELS['capture']}] lock  "
                f"[{CV2_BIND_LABELS['lock_mode']}] size  "
                f"[{CV2_BIND_LABELS['center_hold']}] center  "
                f"[{CV2_BIND_LABELS['hide']}] hide",
                (10, h - 10), font, 0.32, (128, 128, 128), 1,
            )
            return

        y = 112
        track_color = (0, 255, 0) if shared_data["tracking_mode"] else (128, 128, 128)
        cv2.putText(
            frame, f"TRACKING [{CV2_BIND_LABELS['tracking']}]: "
            f"{'ON' if shared_data['tracking_mode'] else 'OFF'}",
            (10, y), font, 0.45, track_color, 1,
        )
        y += 18
        center_color = (0, 255, 0) if shared_data.get("center_hold") else (128, 128, 128)
        cv2.putText(
            frame, f"CENTER [{CV2_BIND_LABELS['center_hold']}]: "
            f"{'ON' if shared_data.get('center_hold') else 'OFF'}",
            (10, y), font, 0.45, center_color, 1,
        )
        y += 18
        thermal_color = (255, 220, 0) if shared_data["thermal_active"] else (128, 128, 128)
        cv2.putText(
            frame, f"THERMAL [{CV2_BIND_LABELS['thermal']}]: "
            f"{'ON' if shared_data['thermal_active'] else 'OFF'} "
            f"{'OVR' if thermal_mode() == 'overlay' else 'WIN'}",
            (10, y), font, 0.45, thermal_color, 1,
        )
        y += 18
        full_color = (0, 255, 0) if shared_data["fullscan_active"] else (128, 128, 128)
        cv2.putText(
            frame, f"FULL [{CV2_BIND_LABELS['fullscan']}]: "
            f"{'ON' if shared_data['fullscan_active'] else 'OFF'}",
            (10, y), font, 0.45, full_color, 1,
        )
        y += 18
        target_color = (0, 255, 0) if targets_count else (128, 128, 128)
        cv2.putText(frame, f"TARGETS: {targets_count}", (10, y), font, 0.45, target_color, 1)


    @staticmethod
    def _tv_anchor() -> Tuple[int, int, bool]:
        """Центр прицела: цель после LOCK, иначе курсор."""
        lock = shared_data.get("lock_target")
        if lock is not None:
            x, y, w, h = lock
            return x + w // 2, y + h // 2, True
        mx, my = win32api.GetCursorPos()
        return mx, my, False

    def _draw_zoom_scope(
        self, painter: QPainter, snap: Optional[FullViewSnapshot],
        x: int, y: int,
    ) -> None:
        """Цифровой TV-прицел по RMB: crop вокруг цели/курсора, x2."""
        if snap is None or snap.size[0] <= 0 or snap.size[1] <= 0:
            return
        thermal_on = bool(shared_data.get("thermal_active"))
        if thermal_on and snap.thermal is not None:
            source = snap.thermal
            is_thermal = True
        elif snap.raw is not None:
            source = snap.raw
            is_thermal = False
        else:
            return

        lens = int(min(self.sw, self.sh) * ZOOM_LENS_FRACTION)
        lens = max(ZOOM_LENS_MIN, min(ZOOM_LENS_MAX, lens))
        src_size = int(round(lens / ZOOM_HOLD_FACTOR))
        src_size = max(24, min(src_size, source.shape[1], source.shape[0]))
        px = x - snap.origin[0]
        py = y - snap.origin[1]
        left = max(0, min(self.sw - lens, x - lens // 2))
        top = max(0, min(self.sh - lens, y - lens // 2))
        crop_x0 = int(round(px - (x - left) * src_size / float(lens)))
        crop_y0 = int(round(py - (y - top) * src_size / float(lens)))

        if source.ndim == 2:
            crop = np.zeros((src_size, src_size), dtype=np.uint8)
        else:
            crop = np.zeros(
                (src_size, src_size, source.shape[2]), dtype=np.uint8
            )
        sh, sw = source.shape[:2]
        sx0, sy0 = max(0, crop_x0), max(0, crop_y0)
        sx1 = min(sw, crop_x0 + src_size)
        sy1 = min(sh, crop_y0 + src_size)
        if sx1 > sx0 and sy1 > sy0:
            dx0, dy0 = sx0 - crop_x0, sy0 - crop_y0
            crop[
                dy0:dy0 + sy1 - sy0, dx0:dx0 + sx1 - sx0
            ] = source[sy0:sy1, sx0:sx1]

        interpolation = (
            cv2.INTER_NEAREST if src_size < lens else cv2.INTER_AREA
        )
        zoomed = cv2.resize(crop, (lens, lens), interpolation=interpolation)
        if is_thermal:
            rgb = cv2.cvtColor(zoomed, cv2.COLOR_BGR2RGB)
            image = QImage(
                rgb.data, lens, lens, lens * 3, QImage.Format_RGB888
            ).copy()
        else:
            image = QImage(
                zoomed.data, lens, lens, lens, QImage.Format_Grayscale8
            ).copy()
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawImage(left, top, image)

        if interface_is_new():
            painter.setPen(QPen(QColor(235, 235, 235, 210), 1))
            painter.drawRect(left, top, lens - 1, lens - 1)
            painter.setPen(QPen(QColor(255, 255, 255, 235), 2))
            corner = max(18, lens // 12)
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                cx = left + (0 if sx < 0 else lens - 1)
                cy = top + (0 if sy < 0 else lens - 1)
                painter.drawLine(cx, cy, cx - sx * corner, cy)
                painter.drawLine(cx, cy, cx, cy - sy * corner)
            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            painter.setPen(QPen(QColor(245, 245, 245, 230), 1))
            painter.drawText(
                left + 8, top + 15,
                f"TV ZOOM x{ZOOM_HOLD_FACTOR:.0f}",
            )
        else:
            painter.setPen(QPen(QColor(210, 210, 210, 180), 1))
            painter.drawRect(left, top, lens - 1, lens - 1)

    def _draw_tv_reticle(
        self, painter: QPainter, x: int, y: int, locked: bool
    ) -> None:
        """Крест, центральный квадрат и внешние углы в стиле TV-ракеты."""
        color = QColor(255, 255, 255, 245) if locked else QColor(235, 235, 235, 220)
        painter.setPen(QPen(color, 1))
        outer = max(160, SQUARE_SIZE)
        half = outer // 2
        corner = max(18, outer // 10)

        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            px = x + sx * half
            py = y + sy * half
            painter.drawLine(px, py, px - sx * corner, py)
            painter.drawLine(px, py, px, py - sy * corner)

        inner = max(80, outer // 3)
        ih = inner // 2
        ic = max(10, inner // 5)
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            px = x + sx * ih
            py = y + sy * ih
            painter.drawLine(px, py, px - sx * ic, py)
            painter.drawLine(px, py, px, py - sy * ic)

        box = max(22, outer // 14)
        painter.drawRect(x - box // 2, y - box // 2, box, box)

        span = max(outer, min(900, self.sh // 2))
        gap = 10
        painter.drawLine(x - span, y, x - gap, y)
        painter.drawLine(x + gap, y, x + span, y)
        painter.drawLine(x, y - span, x, y - gap)
        painter.drawLine(x, y + gap, x, y + span)

        if locked:
            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            painter.setPen(QPen(QColor(255, 255, 255, 235), 1))
            painter.drawText(x - half, max(10, y - half - 6), "TV LOCK")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            if self.is_hidden:
                return

            active = bool(shared_data["active"])
            full = bool(shared_data["fullscan_active"])
            lock = shared_data.get("lock_target")
            thermal_overlay = bool(
                shared_data["thermal_active"] and thermal_mode() == "overlay"
            )
            zoom_hold = bool(shared_data.get("zoom_hold"))
            ui_new = interface_is_new()
            snap = (
                self.thermal_worker.snapshot()
                if (thermal_overlay or zoom_hold)
                else None
            )

            if thermal_overlay and snap is not None:
                painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                painter.fillRect(
                    self.rect(),
                    QColor(0, 0, 0, int(THERMAL_SETTINGS["bg_alpha"])),
                )
                if snap.overlay is not None and snap.size[0] > 0:
                    key = (id(snap.overlay), snap.overlay.shape)
                    if key != self._thermal_qimage_key:
                        height, width = snap.overlay.shape[:2]
                        self._thermal_qimage_data = snap.overlay
                        self._thermal_qimage = QImage(
                            snap.overlay.data,
                            width,
                            height,
                            width * 4,
                            QImage.Format_ARGB32,
                        )
                        self._thermal_qimage_key = key
                    if (
                        self._thermal_qimage is not None
                        and not self._thermal_qimage.isNull()
                    ):
                        painter.drawImage(
                            int(snap.origin[0]),
                            int(snap.origin[1]),
                            self._thermal_qimage,
                        )

                painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
                painter.setFont(QFont("Consolas", 9, QFont.Bold))
                painter.drawText(
                    14, 20, "[ TV THERMAL ]" if ui_new else "[ THERMAL ]"
                )
                painter.setFont(QFont("Consolas", 8))
                painter.setPen(QPen(QColor(210, 210, 210, 220), 1))
                painter.drawText(
                    14,
                    34,
                    f"THR:{THERMAL_SETTINGS['threshold']}  "
                    f"DOT:{THERMAL_SETTINGS['dot_size']}px  "
                    f"STEP:{THERMAL_SETTINGS['sample_step']}  "
                    f"BG:{THERMAL_SETTINGS['bg_alpha']}  "
                    f"[{BIND_LABELS['thermal_mode']}] mode",
                )
                if not snap.found:
                    painter.setPen(QPen(QColor(255, 90, 90, 230), 1))
                    painter.drawText(14, 48, "ROBLOX WINDOW NOT FOUND")

            anchor_x, anchor_y, locked = self._tv_anchor()

            if (
                not active
                and lock is None
                and not full
                and not thermal_overlay
                and not zoom_hold
            ):
                return

            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            if not ui_new and (active or lock is not None or full):
                mx, my = win32api.GetCursorPos()
                half = SQUARE_SIZE // 2
                painter.setPen(QPen(QColor(0, 255, 0, 80), 1))
                painter.drawEllipse(
                    mx - half, my - half, SQUARE_SIZE, SQUARE_SIZE
                )

            if full:
                for rect in shared_data["full_targets"]:
                    rx, ry, rw, rh = rect
                    is_main = (not locked) and rect == shared_data["full_main"]
                    color = QColor(0, 255, 120) if is_main else QColor(90, 220, 140)
                    painter.setPen(QPen(color, 2 if is_main else 1))
                    painter.drawRect(rx, ry, rw, rh)
                    if is_main:
                        painter.setPen(
                            QPen(
                                QColor(color.red(), color.green(), color.blue(), 55),
                                2, Qt.DashLine,
                            )
                        )
                        painter.drawLine(
                            anchor_x, anchor_y, rx + rw // 2, ry + rh // 2
                        )

            if active:
                for rect in shared_data["targets"]:
                    rx, ry, rw, rh = rect
                    is_main = (not locked) and rect == shared_data["main_target"]
                    color = QColor(255, 0, 0) if is_main else QColor(255, 140, 0)
                    painter.setPen(QPen(color, 2 if is_main else 1))
                    painter.drawRect(rx, ry, rw, rh)

                    painter.setPen(
                        QPen(QColor(color.red(), color.green(), color.blue(), 60), 3, Qt.DashLine)
                    )
                    painter.drawLine(anchor_x, anchor_y, rx + rw // 2, ry + rh // 2)

            if lock is not None:
                lx, ly, lw, lh = lock
                painter.setPen(QPen(QColor(255, 255, 255, 235), 2))
                painter.drawRect(lx, ly, lw, lh)
                painter.setFont(QFont("Consolas", 8, QFont.Bold))
                painter.drawText(lx, max(12, ly - 4), "LOCK")

            if zoom_hold and snap is not None:
                self._draw_zoom_scope(painter, snap, anchor_x, anchor_y)

            if ui_new:
                self._draw_tv_reticle(painter, anchor_x, anchor_y, locked)

            if shared_data.get("center_hold"):
                painter.setFont(QFont("Consolas", 8, QFont.Bold))
                painter.setPen(QPen(QColor(245, 245, 245, 225), 1))
                painter.drawText(
                    14,
                    62 if thermal_overlay else 20,
                    "[ CENTER HOLD ]",
                )
        finally:
            painter.end()


    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.timer.stop()
            self.scanner.stop()
            self.thermal_worker.stop()
            self._release_center_inputs()
            save_config()
            if self._radar_window_created:
                try:
                    cv2.destroyAllWindows()
                except Exception:
                    pass
                self._radar_window_created = False
        finally:
            app = QApplication.instance()
            if app is not None:
                app.quit()


def main() -> None:
    app = QApplication(sys.argv)
    overlay = Overlay()
    overlay.show()
    app.aboutToQuit.connect(save_config)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()


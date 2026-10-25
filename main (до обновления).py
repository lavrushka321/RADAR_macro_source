#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RADAR Macro для Python 3.11.9 (локальная версия)
"""

import copy
import ctypes
import json
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

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QMainWindow


__version__ = "beta-3.1 (local)"

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


# Вызывается до QApplication и GetSystemMetrics: иначе при масштабировании
# Windows оверлей и координаты целей окажутся в разных системах координат.
enable_dpi_awareness()


# ==================== КОНФИГ ====================

CONFIG_DIR = Path.home() / ".radar_macro"
CONFIG_FILE = CONFIG_DIR / "config.json"

CONFIG_VERSION = 3

DEFAULT_SETTINGS: Dict[str, Any] = {
    "CONFIG_VERSION": CONFIG_VERSION,
    "SQUARE_SIZE": 400,
    "SCAN_INTERVAL": 0.05,
    "SHOW_RADAR_VIEW": True,
    # Усиление сырой карты движения в окне RADAR_VIEW (до масок она тёмная).
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
            # Убирает одиночные пиксели шума до дилатации, чтобы они не
            # склеивались в крупные ложные блобы.
            "open": 3,
            "min_seed": 30,
            "min_area": 120,
            "max_area": 8000,
            # Плотность контура и вытянутость: трава/ветки дают тонкие
            # и разреженные блобы, цель — компактный.
            "min_fill": 0.2,
            "max_aspect": 4.0,
            # При повороте камеры меняется почти весь кадр — пропускаем такой
            # скан, чтобы не сыпать ложными целями.
            "max_motion_ratio": 0.85,
            # Порог автоматически поднимается на подвижном фоне.
            "adaptive": True,
            "min_hits": 3,
            # Один пропущенный скан не сбрасывает подтверждение цели.
            "max_misses": 1,
            "match_dist": 48,
        },
    },
    # Ручной захват по шаблону внешнего вида (работает, даже если радар
    # не нашёл цель). Шаблон ищется только рядом с прошлой позицией и
    # переживает короткие перекрытия (трассеры, вспышки).
    "LOCK_SETTINGS": {
        "template_size": 48,
        "search_margin": 40,
        "wide_margin": 120,
        "match_min": 0.45,
        "wide_match_min": 0.60,
        "max_misses": 8,
        # После обычных промахов захват не сбрасывается сразу: ещё
        # lost_timeout секунд шаблон ищется в радиусе lost_margin вокруг
        # последней позиции (цель может выйти из кадра/уйти за укрытие).
        "lost_margin": 220,
        "lost_timeout": 2.0,
        "lost_match_min": 0.70,
        "update_score": 0.85,
    },
    "THERMAL_SETTINGS": {
        "threshold": 7,
        "dot_size": 1,
        "sample_step": 1,
        "bg_alpha": 140,
        "max_dots": 20000,
    },
    # window  — тепловая карта в мини-окне RADAR_VIEW;
    # overlay — полупрозрачный слой на весь экран.
    "THERMAL_MODE": "window",
    "BINDS": {
        "key_scan": "F",
        "key_thermal": "Q",
        # Полноэкранный поиск целей по активному профилю. Работает отдельно
        # от тепловизора: Q показывает тепловую карту, H ищет цели.
        "key_fullscan": "H",
        # Переключение места показа тепловизора: окно RADAR_VIEW / оверлей.
        "key_thermal_mode": "Y",
        "key_tracking": "V",
        "key_profile": "G",
        "key_zoom": "Z",
        "key_capture": "T",
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
    if number != number:  # NaN
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
    cfg["MOTION_MAP_GAIN"] = _as_int(cfg.get("MOTION_MAP_GAIN"), 8, 1, 32)

    lock = cfg.get("LOCK_SETTINGS")
    if not isinstance(lock, dict):
        lock = copy.deepcopy(DEFAULT_SETTINGS["LOCK_SETTINGS"])
        cfg["LOCK_SETTINGS"] = lock
    lock["template_size"] = _as_int(lock.get("template_size"), 48, 16, 160)
    lock["search_margin"] = _as_int(lock.get("search_margin"), 40, 8, 300)
    lock["wide_margin"] = _as_int(lock.get("wide_margin"), 120, 8, 500)
    if lock["wide_margin"] < lock["search_margin"]:
        lock["wide_margin"] = lock["search_margin"]
    lock["match_min"] = round(_as_float(lock.get("match_min"), 0.45, 0.05, 0.95), 2)
    lock["wide_match_min"] = round(
        _as_float(lock.get("wide_match_min"), 0.60, 0.05, 0.99), 2
    )
    if lock["wide_match_min"] < lock["match_min"]:
        lock["wide_match_min"] = lock["match_min"]
    lock["max_misses"] = _as_int(lock.get("max_misses"), 8, 0, 60)
    lock["lost_margin"] = _as_int(lock.get("lost_margin"), 220, 8, 800)
    if lock["lost_margin"] < lock["wide_margin"]:
        lock["lost_margin"] = lock["wide_margin"]
    lock["lost_timeout"] = round(
        _as_float(lock.get("lost_timeout"), 2.0, 0.0, 10.0), 2
    )
    lock["lost_match_min"] = round(
        _as_float(lock.get("lost_match_min"), 0.70, 0.05, 0.99), 2
    )
    if lock["lost_match_min"] < lock["wide_match_min"]:
        lock["lost_match_min"] = lock["wide_match_min"]
    lock["update_score"] = round(_as_float(lock.get("update_score"), 0.85, 0.5, 0.99), 2)

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

    # После миграции значения живут в SEARCH_PROFILES; старые ключи убираем,
    # чтобы не было двух источников правды.
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

    # Миграция формата профилей. В версии 3 профиль "noise" получил
    # адаптивный порог, открытие, фильтры формы и допуск пропущенных сканов —
    # старые значения заменяем новыми дефолтами. Профиль precision не трогаем.
    try:
        raw_version = int(raw.get("CONFIG_VERSION", 0))
    except (TypeError, ValueError):
        raw_version = 0
    if raw_version < CONFIG_VERSION:
        profiles = raw.get("SEARCH_PROFILES")
        if isinstance(profiles, dict) and "noise" in profiles:
            profiles["noise"] = copy.deepcopy(DEFAULT_SETTINGS["SEARCH_PROFILES"]["noise"])
        raw["CONFIG_VERSION"] = CONFIG_VERSION

    # Старый конфиг без профилей: переносим его значения в профиль "precision",
    # чтобы поведение после обновления не изменилось.
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


# ==================== КЛАВИШИ ====================

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
        # Для ASCII-букв и цифр VK совпадает с кодом символа при любой раскладке.
        if "A" <= name <= "Z" or "0" <= name <= "9":
            return ord(name)
        vk = win32api.VkKeyScan(raw) & 0xFF
        if vk not in (0, 255):
            return vk

    raise ValueError(f"неизвестная клавиша: {raw!r}")


# ==================== НАСТРОЙКИ ====================

config: Dict[str, Any] = load_config()

SQUARE_SIZE = int(config["SQUARE_SIZE"])
SCAN_INTERVAL = float(config["SCAN_INTERVAL"])
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
VK_CAPTURE = _bind("key_capture", "T")
VK_EXIT = _bind("key_exit", "ESCAPE")
VK_HIDE = _bind("key_hide", "Ё")

BIND_LABELS: Dict[str, str] = {
    name: str(_binds_cfg.get(f"key_{name}", default)).upper()
    for name, default in (
        ("scan", "F"), ("thermal", "Q"), ("fullscan", "H"),
        ("thermal_mode", "Y"), ("tracking", "V"), ("profile", "G"),
        ("zoom", "Z"), ("capture", "T"), ("exit", "ESCAPE"),
        ("hide", "Ё"),
    )
}

# cv2.putText не умеет кириллицу — для окна RADAR_VIEW и полного вида
# держим ASCII-вариант подписей. Ё — рабочая клавиша скрытия, поэтому
# показываем её транслитерацией, а не вопросительным знаком.
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


# ==================== СОСТОЯНИЕ ====================

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
}

# ==================== ЗАХВАТ ОКНА ====================

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

# Явные прототипы: без argtypes/restype ctypes считает хендлы 32-битными и
# на x64 может обрезать указатель.
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
        # Окно может исчезнуть между EnumWindows и проверкой — не роняем
        # перечисление из-за одного такого окна.
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

        # 32 бита на пиксель: stride всегда width*4, без выравнивания строк и
        # без записи за пределы буфера (в 24-битном режиме stride добивается
        # до 4 байт, а буфер выделялся ровно width*height*3).
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

# ==================== ПОИСК ЦЕЛЕЙ ====================

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
        # 98-й процентиль отражает фон, а не маленькую цель: на спокойном
        # кадре он низкий и порог остаётся базовым, на подвижном — растёт.
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

    # Отсекаем слишком маленькие «семена» до дилатации: иначе одно пятно
    # шума после расширения на 21 px превращается в блоб, проходящий min_area.
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

        # Ручной захват: шаблон внешнего вида + последняя позиция в координатах
        # клиентской области окна. Живёт независимо от детектора движения.
        self._manual_template: Optional[np.ndarray] = None
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
                # Ручной захват живёт независимо от F: его состояние не
                # сбрасываем. Поток сканера продолжит искать шаблон в фоне,
                # даже когда радарный поиск выключен.

    def set_manual_lock(
        self,
        template: np.ndarray,
        center: Tuple[float, float],
        rect: Optional[Rect] = None,
    ) -> None:
        """Захватить цель по шаблону из точки под курсором."""
        with self._lock:
            self._manual_template = template.copy()
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
                    # Ждём F или новый ручной захват (T).
                    self._active_evt.wait(0.05)
                    continue
                # F отпущена, но хардлок продолжает искать цель в фоне.
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

            # Держим целевой период, но не копим очередь кадров.
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
        prev_gray = self._prev_gray
        prev_origin = self._prev_origin
        self._prev_gray = gray
        self._prev_origin = origin

        # Ручной захват ведётся по полному кадру окна и не зависит от того,
        # попала ли цель в квадрат радара или в детектор движения.
        self._update_manual_lock(gray, origin)

        client_h, client_w = gray.shape
        mx, my = win32api.GetCursorPos()
        half = size // 2
        # Квадрат поиска вокруг курсора, но только внутри клиентской области.
        left = mx - origin[0] - half
        top = my - origin[1] - half
        x0 = max(0, left)
        y0 = max(0, top)
        x1 = min(client_w, left + size)
        y1 = min(client_h, top + size)
        if x1 <= x0 or y1 <= y0:
            return [], blank

        if prev_gray is None or prev_origin != origin or prev_gray.shape != gray.shape:
            # Первый кадр после включения/перемещения окна: нужно два кадра.
            return [], blank

        raw = cv2.absdiff(prev_gray[y0:y1, x0:x1], gray[y0:y1, x0:x1])

        # Сырая карта движения до threshold/blur/морфологии — её показывает
        # окно RADAR_VIEW.
        motion = blank.copy()
        motion[y0 - top:y1 - top, x0 - left:x1 - left] = raw

        diff = raw
        targets: List[Rect] = []
        for tx, ty, tw, th in analyze_motion(diff, profile):
            targets.append((origin[0] + x0 + tx, origin[1] + y0 + ty, tw, th))
        return targets, motion

    # -------- ручной захват --------

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
    ) -> Optional[Tuple[Tuple[float, float], float]]:
        h, w = gray.shape
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
                    self._manual_rect = None
                    self._manual_last_seen = None
                    self._manual_lost_at = None
            return

        predicted = (center[0] + velocity[0], center[1] + velocity[1])
        settings = LOCK_SETTINGS
        max_misses = int(settings["max_misses"])
        match = self._match_template(
            gray, template, predicted, int(settings["search_margin"])
        )
        accepted = match is not None and match[1] >= float(settings["match_min"])
        lost_mode = not accepted and misses + 1 > max_misses
        matched_via_lost = False

        if not accepted and not lost_mode and misses % 2 == 0:
            # Широкий поиск не каждый скан: он дороже, а нужен только когда
            # цель резко сместилась или её кратко перекрыли.
            wide = self._match_template(
                gray, template, predicted, int(settings["wide_margin"])
            )
            if (
                wide is not None
                and wide[1] >= float(settings["wide_match_min"])
                and (match is None or wide[1] > match[1] + 0.05)
            ):
                match = wide
                accepted = True
                lost_mode = False

        if not accepted and lost_mode:
            # Обычный лимит промахов исчерпан: продолжаем искать в большем
            # радиусе вокруг последней подтверждённой позиции. Более высокий
            # порог lost_match_min защищает от ложного захвата фона.
            if lost_at is None:
                lost_at = now
            lost = self._match_template(
                gray, template, last_seen, int(settings["lost_margin"])
            )
            if lost is not None and lost[1] >= float(settings["lost_match_min"]):
                match = lost
                accepted = True
                matched_via_lost = True

        if accepted and match is not None:
            new_center, score = match
            dx = new_center[0] - center[0]
            dy = new_center[1] - center[1]
            jump = (dx * dx + dy * dy) ** 0.5
            if matched_via_lost or jump > float(settings["wide_margin"]):
                # Резкий прыжок — это скорее перезахват, чем обычное движение:
                # не разгоняем рамку по инерции в сторону от цели.
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
            # Обновляем шаблон только при уверенном совпадении, чтобы трассер
            # или вспышка не «впечатались» в эталон. Для lost-поиска порог
            # обновления ещё выше: слабое совпадение не должно уводить эталон.
            if score >= float(settings["update_score"]) and (
                not matched_via_lost or score >= 0.90
            ):
                patch = self._patch_at(gray, new_center, tw, th)
                if patch is not None:
                    template = cv2.addWeighted(template, 0.8, patch, 0.2, 0)
        else:
            misses += 1
            if lost_mode:
                # В режиме поиска рамка стоит на последней подтверждённой
                # позиции, а не уезжает по инерции: ищем «от себя».
                new_center = last_seen
                velocity = (0.0, 0.0)
            else:
                new_center = predicted
                velocity = (velocity[0] * 0.9, velocity[1] * 0.9)
                # Не уводим рамку далеко от последней подтверждённой точки:
                # потерянную цель всё равно ищем в lost_margin от неё.
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
            self._manual_center = new_center
            self._manual_last_seen = last_seen
            self._manual_velocity = velocity
            self._manual_misses = misses
            self._manual_score = score
            self._manual_rect = rect
            self._manual_lost_at = lost_at

# ==================== ТЕПЛОВИЗОР (ПОТОК) ====================

class FullViewSnapshot(NamedTuple):
    """Один кадр полного окна Roblox для RADAR_VIEW.

    ``thermal`` — цветная тепловая карта (BGR) для RADAR_VIEW, ``overlay`` —
    прозрачный BGRA-слой для полноэкранного оверлея, ``motion`` — сырая карта
    разницы кадров (grayscale). ``targets`` уже в экранных координатах,
    ``origin``/``size`` нужны для пересчёта рамок в координаты окна.
    """

    thermal: Optional[np.ndarray]
    overlay: Optional[np.ndarray]
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
            None, None, None, [], None, origin, size, found, 0
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
                # Не показываем старую тепловую карту, пока воркер не сделает
                # следующий кадр уже без Q.
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
                # Рамки полного поиска должны исчезнуть сразу, не дожидаясь
                # следующего кадра; тепловая карта при включённом Q остаётся.
                self._snapshot = self._snapshot._replace(
                    targets=[], main=None, motion=None
                )
        self._refresh_active()

    def set_active(self, active: bool) -> None:
        """Совместимость со старым API: включает только тепловую карту."""
        self.set_thermal(active)

    def _refresh_active(self) -> None:
        with self._lock:
            active = self._thermal_active or self._fullscan_active
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
            # Списки отдаём копией: UI читает их из другого потока.
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
                # Режим могли выключить, пока шёл захват/детект. Не отдаём
                # в UI уже отключённые слои даже на один кадр.
                if not self._fullscan_active:
                    snap = snap._replace(targets=[], main=None, motion=None)
                if not self._thermal_active:
                    snap = snap._replace(thermal=None, overlay=None)
                elif thermal_mode() == "window":
                    snap = snap._replace(overlay=None)
                else:
                    snap = snap._replace(thermal=None)
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
        want_window = thermal_on and mode == "window"
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
                thermal, overlay, None, [], None, origin, (width, height), True, 0
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

        # Сырая карта нужна, когда H включён без тепловизора. Если включён
        # только Q, показываем тепловую карту и не тратим память на копию.
        motion = diff if fullscan_on else None
        return FullViewSnapshot(
            thermal, overlay, motion, list(targets), main, origin,
            (width, height), True, count,
        )

    @staticmethod
    def _thermal_bg(width: int, height: int) -> np.ndarray:
        """Тёмный фон тепловой карты (цвет BGR)."""
        # В режиме window bg_alpha задаёт яркость фона RADAR_VIEW, в режиме
        # overlay — прозрачность затемнения всего экрана.
        bg = max(0, min(80, int(THERMAL_SETTINGS.get("bg_alpha", 140)) // 4))
        return np.full((height, width, 3), bg, dtype=np.uint8)

    @staticmethod
    def _thermal_image(
        diff: np.ndarray, want_window: bool, want_overlay: bool
    ) -> Tuple[
        Optional[np.ndarray], Optional[np.ndarray], int
    ]:
        """Цветная тепловая карта всего окна по разнице кадров.

        ``want_window`` — BGR-карта для RADAR_VIEW, ``want_overlay`` — BGRA-слой
        для полупрозрачного полноэкранного оверлея (фон прозрачный).
        Порог, шаг выборки и размер точки берутся из THERMAL_SETTINGS.
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
            hot = cv2.convertScaleAbs(hot, alpha=gain)
            heat = cv2.applyColorMap(hot, cv2.COLORMAP_INFERNO)

        thermal: Optional[np.ndarray] = None
        if want_window:
            thermal = ThermalWorker._thermal_bg(width, height)
            if heat is not None:
                thermal[mask != 0] = heat[mask != 0]

        overlay: Optional[np.ndarray] = None
        if want_overlay:
            # BGRA: QImage.Format_ARGB32 на Windows читает байты B,G,R,A.
            overlay = np.zeros((height, width, 4), dtype=np.uint8)
            if heat is not None:
                overlay[mask != 0, :3] = heat[mask != 0]
                overlay[mask != 0, 3] = 255

        return thermal, overlay, count

# ==================== ОВЕРЛЕЙ РАДАРА ====================

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
        self._next_paint = 0.0
        self._next_view = 0.0
        self._next_topmost = 0.0
        self._radar_hwnd: Optional[int] = None
        self._radar_window_created = False
        self._radar_zoom = False
        self._track_next = 0.0

        # Кэш BGRA-слоя тепловизора для полноэкранного оверлея: QImage
        # ссылается на numpy-буфер, поэтому храним и сам массив.
        self._thermal_qimage: Optional[QImage] = None
        self._thermal_qimage_key: Optional[Tuple[int, Tuple[int, ...]]] = None
        self._thermal_qimage_data: Optional[np.ndarray] = None

        # Ручной захват (хардлок): помним, было ли слежение V включено до него.
        # Если захват сам включил V, после снятия/срыва вернём его в OFF.
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

    # -------- видимость --------

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
            self.clear_manual_lock()
            self.scanner.set_active(False)
            self.thermal_worker.set_thermal(False)
            self.thermal_worker.set_fullscan(False)
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

    # -------- наведение --------

    @staticmethod
    def target_center(rect: Rect) -> Tuple[int, int]:
        x, y, w, h = rect
        return x + w // 2, y + h // 2

    def execute_tracking(self, rect: Rect) -> None:
        now = time.perf_counter()
        if now < self._track_next:
            return
        self._track_next = now + 0.04
        try:
            win32api.SetCursorPos(self.target_center(rect))
        except Exception:
            pass

    # -------- профили --------

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

    # -------- ручной захват --------

    def toggle_manual_lock(self) -> None:
        """Захват цели под курсором по внешнему виду (без детекта движения)."""
        if self.scanner.has_manual_lock():
            self._finish_manual_lock("released")
            return

        captured = capture_roblox_window()
        if captured is None:
            print("[lock] окно Roblox не найдено")
            return

        gray, origin = captured
        size = int(LOCK_SETTINGS["template_size"])
        mx, my = win32api.GetCursorPos()
        h, w = gray.shape
        left = int(round(mx - origin[0] - size / 2.0))
        top = int(round(my - origin[1] - size / 2.0))
        if left < 0 or top < 0 or left + size > w or top + size > h:
            print("[lock] курсор вне окна Roblox")
            return

        patch = gray[top:top + size, left:left + size].copy()
        if float(patch.std()) < 2.0:
            print("[lock] область слишком однородная — наведите на цель")
            return

        # Координаты рамки на экране — сразу, чтобы LOCK был виден даже без
        # активного поиска (когда сканер ещё не сделал ни одного кадра).
        screen_rect = (origin[0] + left, origin[1] + top, size, size)

        # Запоминаем состояние V до захвата: если слежение было выключено,
        # после снятия или срыва захвата вернём его в OFF.
        self._lock_tracking_before = bool(shared_data["tracking_mode"])
        self._lock_tracking_auto = False
        self._manual_lock_owned = True
        self.scanner.set_manual_lock(
            patch, (left + size / 2.0, top + size / 2.0), screen_rect
        )
        shared_data["lock_target"] = screen_rect
        # Слежение включаем только при активном поиске. Без поиска захват
        # остаётся включённым, но курсор за целью не ведётся.
        if keys.pressed(VK_SCAN) and not self._lock_tracking_before:
            shared_data["tracking_mode"] = True
            self._lock_tracking_auto = True
        self.update()
        print(f"[lock] target captured ({size}x{size})")

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

    # -------- тепловизор и полный поиск --------

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
            # Настройки тепловизора меняются Num-клавишами; фиксируем их на
            # диск при выключении, не дожидаясь выхода.
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

    # -------- горячие клавиши --------

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
            # V управляет только слежением: ручной захват (LOCK) остаётся,
            # чтобы его можно было выключить/включить при активном хардлоке.
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
        self._handle_thermal_hotkeys()

    # -------- главный цикл --------

    def logic_tick(self) -> None:
        if self._closing:
            return
        self._poll_hotkeys()
        if self._closing or self.is_hidden:
            return

        active = keys.pressed(VK_SCAN)
        full_active = bool(shared_data["fullscan_active"])
        thermal_overlay = bool(
            shared_data["thermal_active"] and thermal_mode() == "overlay"
        )
        state_changed = active != self._prev_active
        full_state_changed = full_active != self._prev_fullscan
        if state_changed or full_state_changed:
            self._next_view = 0.0
        shared_data["active"] = active
        self.scanner.set_active(active)

        # Полный вид и поиск живут в отдельном воркере: он захватывает всё
        # окно Roblox даже при отпущенной F.
        full_snap = self.thermal_worker.snapshot() if (
            shared_data["thermal_active"] or full_active
        ) else None
        if full_active and full_snap is not None:
            shared_data["full_targets"] = list(full_snap.targets)
            shared_data["full_main"] = full_snap.main
        else:
            shared_data["full_targets"] = []
            shared_data["full_main"] = None
        full_main = shared_data["full_main"]

        if active:
            targets, main, motion, lock = self.scanner.snapshot()
            # Сканер потерял шаблон (цель ушла/перекрыта дольше max_misses):
            # снимаем хардлок и возвращаем слежение в состояние до захвата.
            if self._manual_lock_owned and not self.scanner.has_manual_lock():
                self._finish_manual_lock("lost")
                lock = None
            shared_data["targets"] = targets
            shared_data["main_target"] = main
            shared_data["lock_target"] = lock
            # Приоритет: ручной LOCK, затем цель курсорного радара, затем
            # главная цель полного поиска.
            steer = lock if lock is not None else (main if main is not None else full_main)
            if shared_data["tracking_mode"] and steer is not None:
                self.execute_tracking(steer)
        else:
            # Срыв мог произойти в последнем скане перед отпусканием F —
            # обрабатываем его и без активного сканирования.
            if self._manual_lock_owned and not self.scanner.has_manual_lock():
                self._finish_manual_lock("lost")
            targets, main, motion = [], None, None
            shared_data["targets"] = []
            shared_data["main_target"] = None
            # Захват при отпущенном F не снимается: сканер продолжает искать
            # шаблон в фоне и обновляет рамку.
            shared_data["lock_target"] = (
                self.scanner.manual_lock_rect() if self._manual_lock_owned else None
            )
            steer = shared_data["lock_target"]
            if steer is None and full_active:
                steer = full_main
            if shared_data["tracking_mode"] and steer is not None:
                self.execute_tracking(steer)

        self._update_radar_view(targets, motion, active, full_snap)

        now = time.perf_counter()
        # Смена состояния (нажал/отпустил F или H) перерисовывается
        # принудительно: иначе throttle мог пропустить единственный кадр
        # очистки, и старые боксы оставались на экране.
        if state_changed or full_state_changed or (
            (
                active
                or self._prev_active
                or self._manual_lock_owned
                or full_active
                or self._prev_fullscan
                or thermal_overlay
            )
            and now >= self._next_paint
        ):
            self._next_paint = now + 0.033
            self.update()
        self._prev_active = active
        self._prev_fullscan = full_active

    # -------- окно RADAR_VIEW --------

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
            # В режиме overlay тепловизор уже рисуется поверх экрана, поэтому
            # мини-окно при H показывает motion map и рамки, без дубля тепла.
            if thermal_mode() == "overlay" and full_snap is not None:
                full_snap = full_snap._replace(thermal=None)
            frame = self._render_full_view(full_snap)
        else:
            frame = np.zeros((SQUARE_SIZE, SQUARE_SIZE, 3), dtype=np.uint8)
            if motion is not None:
                gain = int(config.get("MOTION_MAP_GAIN", 8))
                scaled = motion if gain == 1 else cv2.convertScaleAbs(motion, alpha=gain)
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
                if x1 + lw > 0 and y1 + lh > 0 and x1 < SQUARE_SIZE and y1 < SQUARE_SIZE:
                    cv2.rectangle(frame, (x1, y1), (x1 + lw, y1 + lh), (255, 255, 0), 1)
                    label_y = y1 - 4 if y1 >= 14 else y1 + lh + 12
                    cv2.putText(
                        frame, "LOCK", (max(2, x1), min(SQUARE_SIZE - 4, label_y)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1,
                    )

        if self._radar_zoom:
            # Увеличиваем уже готовый кадр целиком: карта, перекрестие и
            # подписи растут вместе с окном, пиксели остаются чёткими.
            frame = cv2.resize(
                frame, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST
            )
        try:
            cv2.imshow(RADAR_WINDOW_TITLE, frame)
            cv2.waitKey(1)
        except Exception as exc:
            print(f"[view] {exc}")
            self._radar_hwnd = None
            self._radar_window_created = False
            return

        # FindWindow/SetWindowPos раньше вызывались 60+ раз в секунду; теперь
        # хендл кэшируется и поднимается не чаще раза в секунду.
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
        """Полное окно Roblox в RADAR_VIEW: тепловая карта или motion map."""
        frame = np.zeros((SQUARE_SIZE, SQUARE_SIZE, 3), dtype=np.uint8)
        if snap is None:
            snap = FullViewSnapshot(
                None, None, None, [], None, (0, 0), (0, 0), False, 0
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

            if source is not None:
                interpolation = (
                    cv2.INTER_AREA if source.shape[1] > width else cv2.INTER_NEAREST
                )
                resized = cv2.resize(source, (width, height), interpolation=interpolation)
                frame[y0:y0 + height, x0:x0 + width] = resized

            cv2.rectangle(
                frame, (x0, y0), (x0 + width - 1, y0 + height - 1), (80, 80, 80), 1
            )

            # Центр клиента Roblox и позиция курсора.
            cv2.drawMarker(
                frame, (x0 + width // 2, y0 + height // 2),
                (55, 55, 55), cv2.MARKER_CROSS, 6, 1,
            )
            mx, my = win32api.GetCursorPos()
            cursor_x = x0 + int(round((mx - snap.origin[0]) * width / float(snap.size[0])))
            cursor_y = y0 + int(round((my - snap.origin[1]) * height / float(snap.size[1])))
            cursor_inside = (
                0 <= mx - snap.origin[0] < snap.size[0]
                and 0 <= my - snap.origin[1] < snap.size[1]
            )
            if cursor_inside:
                cv2.drawMarker(
                    frame, (cursor_x, cursor_y), (0, 255, 0),
                    cv2.MARKER_CROSS, 8, 1,
                )

            locked = shared_data.get("lock_target") is not None
            for rect in snap.targets:
                mapped = self._map_full_rect(
                    rect, snap.origin, snap.size, geometry
                )
                if mapped is None:
                    continue
                tx, ty, tw, th = mapped
                is_main = (not locked) and rect == snap.main
                color = (0, 255, 0) if is_main else (0, 175, 90)
                cv2.rectangle(
                    frame, (tx, ty), (tx + tw, ty + th), color, 2 if is_main else 1
                )
                if is_main:
                    start = (cursor_x, cursor_y) if cursor_inside else (
                        x0 + width // 2, y0 + height // 2
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
                mapped = self._map_full_rect(
                    lock, snap.origin, snap.size, geometry
                )
                if mapped is not None:
                    lx, ly, lw, lh = mapped
                    cv2.rectangle(
                        frame, (lx, ly), (lx + lw, ly + lh), (255, 255, 0), 2
                    )
                    label_y = ly - 4 if ly >= 14 else ly + lh + 12
                    cv2.putText(
                        frame, "LOCK", (max(2, lx), min(SQUARE_SIZE - 4, label_y)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1,
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
                f"ZOOM [{CV2_BIND_LABELS['zoom']}]: x{2 if self._radar_zoom else 1}",
                (255, 255, 0) if lock_on else (150, 150, 150),
            ),
        ]
        spacing = max(12, min(18, (top - 8) // 6))
        font_scale = 0.38 if top >= 80 else 0.32
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

        # Нижняя подсказка по клавишам.
        hint_panel = frame.copy()
        cv2.rectangle(hint_panel, (0, height - bottom), (width, height), (0, 0, 0), -1)
        cv2.addWeighted(hint_panel, 0.72, frame, 0.28, 0, frame)
        if bottom >= 26:
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['scan']}] scan  [{CV2_BIND_LABELS['tracking']}] track  "
                f"[{CV2_BIND_LABELS['thermal']}] thermal  [{CV2_BIND_LABELS['fullscan']}] full  "
                f"[{CV2_BIND_LABELS['capture']}] lock",
                (10, height - bottom + 12), font, 0.32, (190, 190, 190), 1,
            )
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['thermal_mode']}] mode  "
                f"[{CV2_BIND_LABELS['zoom']}] zoom  "
                f"[{CV2_BIND_LABELS['hide']}] hide  [ESC] exit",
                (10, height - 5), font, 0.32, (190, 190, 190), 1,
            )
        else:
            cv2.putText(
                frame,
                f"[{CV2_BIND_LABELS['scan']}] scan [{CV2_BIND_LABELS['thermal']}] thermal "
                f"[{CV2_BIND_LABELS['fullscan']}] full [{CV2_BIND_LABELS['capture']}] lock",
                (10, height - 4), font, 0.28, (190, 190, 190), 1,
            )

    @staticmethod
    def _draw_radar_status(
        frame: np.ndarray, active: bool, targets_count: int, zoomed: bool = False
    ) -> None:
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        cx, cy = w // 2, h // 2
        cv2.line(frame, (0, cy), (w, cy), (60, 60, 60), 1)
        cv2.line(frame, (cx, 0), (cx, h), (60, 60, 60), 1)
        cv2.circle(frame, (cx, cy), 2, (0, 180, 0), -1)

        # cv2.putText не умеет кириллицу — показываем ключ профиля.
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
        lock_color = (255, 255, 0) if shared_data.get("lock_target") else (128, 128, 128)
        cv2.putText(
            frame,
            f"LOCK [{CV2_BIND_LABELS['capture']}]: "
            f"{'ON' if shared_data.get('lock_target') else 'OFF'}",
            (10, 74), font, 0.4, lock_color, 1,
        )

        if not active:
            cv2.putText(
                frame, f"HOLD {CV2_BIND_LABELS['scan']} TO SCAN",
                (10, cy), font, 0.55, (255, 255, 255), 1,
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
                f"[{CV2_BIND_LABELS['hide']}] hide  [ESC] exit",
                (10, h - 10), font, 0.32, (128, 128, 128), 1,
            )
            return

        y = 94
        track_color = (0, 255, 0) if shared_data["tracking_mode"] else (128, 128, 128)
        cv2.putText(
            frame, f"TRACKING [{CV2_BIND_LABELS['tracking']}]: "
            f"{'ON' if shared_data['tracking_mode'] else 'OFF'}",
            (10, y), font, 0.5, track_color, 1,
        )
        y += 20
        thermal_color = (255, 220, 0) if shared_data["thermal_active"] else (128, 128, 128)
        cv2.putText(
            frame, f"THERMAL [{CV2_BIND_LABELS['thermal']}]: "
            f"{'ON' if shared_data['thermal_active'] else 'OFF'} "
            f"{'OVR' if thermal_mode() == 'overlay' else 'WIN'}",
            (10, y), font, 0.5, thermal_color, 1,
        )
        y += 20
        full_color = (0, 255, 0) if shared_data["fullscan_active"] else (128, 128, 128)
        cv2.putText(
            frame, f"FULL [{CV2_BIND_LABELS['fullscan']}]: "
            f"{'ON' if shared_data['fullscan_active'] else 'OFF'}",
            (10, y), font, 0.5, full_color, 1,
        )
        y += 20
        target_color = (0, 255, 0) if targets_count else (128, 128, 128)
        cv2.putText(frame, f"TARGETS: {targets_count}", (10, y), font, 0.5, target_color, 1)

    # -------- отрисовка оверлея --------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            # Прозрачный слой чистим явно каждый раз: при отпускании F не
            # должны оставаться круг и старые боксы, но LOCK остаётся видимым.
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

            # Режим overlay: затемняем весь экран и рисуем только горячие
            # пиксели тепловой карты поверх окна Roblox. Рамки H и радара
            # рисуются выше, поэтому остаются читаемыми.
            if thermal_overlay:
                painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                painter.fillRect(
                    self.rect(),
                    QColor(0, 0, 0, int(THERMAL_SETTINGS["bg_alpha"])),
                )
                snap = self.thermal_worker.snapshot()
                if snap.overlay is not None and snap.size[0] > 0:
                    key = (id(snap.overlay), snap.overlay.shape)
                    if key != self._thermal_qimage_key:
                        height, width = snap.overlay.shape[:2]
                        # QImage не копирует буфер — держим numpy-массив живым.
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
                painter.drawText(14, 20, "[ THERMAL OVERLAY ]")
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

            if not active and lock is None and not full:
                return

            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            mx, my = win32api.GetCursorPos()
            half = SQUARE_SIZE // 2
            locked = lock is not None
            center_x, center_y = shared_data["screen_center"]

            # Полноэкранный поиск рисует рамки по всему клиенту Roblox и
            # работает независимо от F и Q. Основная цель — ярче.
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
                            center_x, center_y, rx + rw // 2, ry + rh // 2
                        )

            if active:
                painter.setPen(QPen(QColor(0, 255, 0, 80), 1))
                painter.drawEllipse(mx - half, my - half, SQUARE_SIZE, SQUARE_SIZE)

                for rect in shared_data["targets"]:
                    rx, ry, rw, rh = rect
                    # При ручном захвате радарные блобы вторичны: не подсвечиваем
                    # «главную» цель красным, чтобы трассер не выглядел захваченным.
                    is_main = (not locked) and rect == shared_data["main_target"]
                    color = QColor(255, 0, 0) if is_main else QColor(255, 140, 0)
                    painter.setPen(QPen(color, 2 if is_main else 1))
                    painter.drawRect(rx, ry, rw, rh)

                    painter.setPen(
                        QPen(QColor(color.red(), color.green(), color.blue(), 60), 3, Qt.DashLine)
                    )
                    painter.drawLine(center_x, center_y, rx + rw // 2, ry + rh // 2)

                painter.setPen(QPen(QColor(0, 220, 120, 170), 1))
                painter.setFont(QFont("Consolas", 8))
                painter.drawText(mx + half + 6, my - half + 12, get_profile()["title"])

            if lock is not None:
                lx, ly, lw, lh = lock
                painter.setPen(QPen(QColor(0, 255, 255), 2))
                painter.drawRect(lx, ly, lw, lh)
                painter.setFont(QFont("Consolas", 8, QFont.Bold))
                painter.drawText(lx, max(12, ly - 4), "LOCK")
        finally:
            painter.end()

    # -------- завершение --------

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.timer.stop()
            self.scanner.stop()
            self.thermal_worker.stop()
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

# ==================== ЗАПУСК ====================

def main() -> None:
    app = QApplication(sys.argv)
    overlay = Overlay()
    overlay.show()
    app.aboutToQuit.connect(save_config)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()


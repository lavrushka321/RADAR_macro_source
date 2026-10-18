#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RADAR Macro для Python 3.11.9 (локальная версия)
"""

import sys
import json
import time
import ctypes
import threading
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

import cv2
import numpy as np
import mss
import win32api
import win32con
import win32gui
import pydirectinput
from pynput import keyboard

from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor, QPainter, QPen


__version__ = "beta-2.4 (local)"


# ==================== КОНФИГ ====================

CONFIG_DIR = Path.home() / ".radar_macro"
CONFIG_DIR.mkdir(exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "SQUARE_SIZE": 400,
    "THRESHOLD_VAL": 7,
    "MIN_TARGET": 0,
    "MAX_MAP_AREA": 10000,
    "SCAN_INTERVAL": 0.05,
    "THERMAL_SETTINGS": {
        "threshold": 7,
        "dot_size": 1,
        "sample_step": 1,
        "bg_alpha": 140,
        "max_dots": 20000,
    },
    "BINDS": {
        "key_scan": "F",
        "key_thermal": "Q",
        "key_tracking": "V",
        "key_exit": "ESCAPE",
        "key_hide": "Ё",
    },
    "THERMAL_WINDOWS": ["Roblox", "Roblox Player", "ROBLOX"],
}


def load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        bundle_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        default_config_path = bundle_dir / "default_config.json"

        if default_config_path.exists():
            import shutil
            shutil.copy2(default_config_path, CONFIG_FILE)
        else:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=4, ensure_ascii=False)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


config: Dict[str, Any] = load_config()

SQUARE_SIZE = config["SQUARE_SIZE"]
THRESHOLD_VAL = config["THRESHOLD_VAL"]
MIN_TARGET = config["MIN_TARGET"]
MAX_MAP_AREA = config["MAX_MAP_AREA"]
SCAN_INTERVAL = config["SCAN_INTERVAL"]

THERMAL_SETTINGS: Dict[str, int] = config["THERMAL_SETTINGS"].copy()
THERMAL_WINDOWS: List[str] = config.get("THERMAL_WINDOWS", ["Roblox", "Roblox Player", "ROBLOX"])

_binds = config.get("BINDS", {})


# ==================== КЛАВИШИ ====================

def resolve_vk(key_name: str) -> int:
    """Переводит имя клавиши в её виртуальный код Windows."""
    name = key_name.upper().strip()

    special = {
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
    }

    if name in special:
        return special[name]
    if name == "Ё":
        return 0xBD
    if name in ("`", "~"):
        return 0xC0
    return ord(name[0])


VK_SCAN = resolve_vk(_binds.get("key_scan", "F"))
VK_THERMAL = resolve_vk(_binds.get("key_thermal", "Q"))
VK_TRACK = resolve_vk(_binds.get("key_tracking", "V"))
VK_EXIT = resolve_vk(_binds.get("key_exit", "ESCAPE"))
VK_HIDE = resolve_vk(_binds.get("key_hide", "Ё"))


class KeyState:
    """Единая точка обработки состояния клавиш (нажато / только что нажато)."""

    def __init__(self) -> None:
        self._prev: Dict[int, bool] = {}

    def pressed(self, vk: int) -> bool:
        """True, пока клавиша удерживается."""
        return bool(win32api.GetAsyncKeyState(vk) & 0x8000)

    def just_pressed(self, vk: int) -> bool:
        """True один раз в момент нажатия (front edge)."""
        now = self.pressed(vk)
        was = self._prev.get(vk, False)
        self._prev[vk] = now
        return now and not was


keys = KeyState()

pydirectinput.PAUSE = 0.01
pydirectinput.FAILSAFE = False


# ==================== СОСТОЯНИЕ ====================

shared_data: Dict[str, Any] = {
    "active": False,
    "tracking_mode": False,
    "screen_center": (win32api.GetSystemMetrics(0) // 2, win32api.GetSystemMetrics(1) // 2),
    "targets": [],
    "main_target": None,

    "thermal_active": False,
    "thermal_prev_frame": None,
    "thermal_diff_points": [],
    "thermal_origin": (0, 0),
    "thermal_size": (0, 0),
    "thermal_found": False,
}

keyboard_listener: Optional[keyboard.Listener] = None
last_q_press_time = 0.0


def _on_q_press() -> None:
    """Ставит флаг, что клавиша Q нажата (обрабатывается в logic_tick)."""
    global last_q_press_time
    now = time.time()
    if now - last_q_press_time > 0.3:
        shared_data["thermal_toggle_request"] = True
        last_q_press_time = now

def on_press(key) -> None:
    try:
        vk = getattr(key, "vk", None)
        if vk == ord("Q"):
            _on_q_press()
            return
        char = getattr(key, "char", None)
        if char and char.lower() in ("q", "й"):
            _on_q_press()
    except Exception:
        pass

def start_global_keyboard_listener() -> None:
    global keyboard_listener
    if keyboard_listener is None or not keyboard_listener.is_alive():
        keyboard_listener = keyboard.Listener(on_press=on_press)
        keyboard_listener.daemon = True
        keyboard_listener.start()


# ==================== ЗАХВАТ ОКНА ====================

def find_roblox_hwnd() -> Optional[int]:
    for title in THERMAL_WINDOWS:
        hwnd = win32gui.FindWindow(None, title)
        if hwnd:
            return hwnd
    return None


def capture_roblox_window() -> Optional[np.ndarray]:
    """Снимок клиентской области окна Roblox в grayscale."""
    hwnd = find_roblox_hwnd()
    if not hwnd:
        return None

    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    w, h = cr - cl, cb - ct
    if w <= 0 or h <= 0:
        return None

    ox, oy = win32gui.ClientToScreen(hwnd, (0, 0))
    shared_data["thermal_origin"] = (ox, oy)
    shared_data["thermal_size"] = (w, h)

    hwnd_dc = win32gui.GetDC(hwnd)
    mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    ctypes.windll.gdi32.SelectObject(mem_dc, bmp)

    if not ctypes.windll.user32.PrintWindow(hwnd, mem_dc, 0x05):
        ctypes.windll.gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, 0x00CC0020)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    bmi = BITMAPINFOHEADER(
        biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
        biPlanes=1, biBitCount=24, biCompression=0,
    )
    buf = (ctypes.c_byte * (w * h * 3))()
    ctypes.windll.gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)

    ctypes.windll.gdi32.DeleteObject(bmp)
    ctypes.windll.gdi32.DeleteDC(mem_dc)
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    img = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


# ==================== ОВЕРЛЕЙ РАДАРА ====================

class Overlay(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1))

        self.sct = mss.mss()
        cv2.namedWindow("RADAR_VIEW", cv2.WINDOW_AUTOSIZE)

        self._thermal_overlay: Optional["ThermalOverlay"] = None
        self.is_hidden = False

        self.timer = QTimer()
        self.timer.timeout.connect(self.logic_tick)
        self.timer.start(15)

        start_global_keyboard_listener()

    # -------- видимость --------

    def toggle_hide(self) -> None:
        if not self.is_hidden:
            self.is_hidden = True
            shared_data["active"] = False
            shared_data["tracking_mode"] = False
            shared_data["thermal_active"] = False

            try:
                cv2.destroyWindow("RADAR_VIEW")
            except Exception:
                pass

            self.hide()
            if self._thermal_overlay is not None:
                self._thermal_overlay.stop()
                self._thermal_overlay.hide()
        else:
            self.is_hidden = False
            self.show()
            self._restore_radar_window()

    def _restore_radar_window(self) -> None:
        try:
            cv2.namedWindow("RADAR_VIEW", cv2.WINDOW_AUTOSIZE)
            hwnd = win32gui.FindWindow(None, "RADAR_VIEW")
            if hwnd:
                win32gui.SetWindowPos(
                    hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
                )
        except Exception as e:
            print(f"RADAR_VIEW restore error: {e}")

    # -------- наведение --------

    @staticmethod
    def target_center(rect: Tuple[int, int, int, int]) -> Tuple[int, int]:
        x, y, w, h = rect
        return x + w // 2, y + h // 2

    def execute_tracking(self, rect: Tuple[int, int, int, int]) -> None:
        try:
            win32api.SetCursorPos(self.target_center(rect))
        except Exception:
            pass

    # -------- thermal --------

    def toggle_thermal(self) -> None:
        if not shared_data["thermal_active"]:
            shared_data["thermal_active"] = True
            if self._thermal_overlay is None:
                self._thermal_overlay = ThermalOverlay()
            self._thermal_overlay.start()
            self._thermal_overlay.show()
        else:
            shared_data["thermal_active"] = False
            if self._thermal_overlay is not None:
                self._thermal_overlay.stop()
                self._thermal_overlay.hide()

    def _handle_thermal_hotkeys(self) -> None:
        s = THERMAL_SETTINGS
        if keys.just_pressed(win32con.VK_ADD):
            s["threshold"] = min(80, s["threshold"] + 1)
        if keys.just_pressed(win32con.VK_SUBTRACT):
            s["threshold"] = max(1, s["threshold"] - 1)
        if keys.just_pressed(win32con.VK_NUMPAD8):
            s["dot_size"] = min(8, s["dot_size"] + 1)
        if keys.just_pressed(win32con.VK_NUMPAD2):
            s["dot_size"] = max(1, s["dot_size"] - 1)
        if keys.just_pressed(win32con.VK_NUMPAD4):
            s["sample_step"] = max(1, s["sample_step"] - 1)
        if keys.just_pressed(win32con.VK_NUMPAD6):
            s["sample_step"] = min(12, s["sample_step"] + 1)
        if keys.just_pressed(win32con.VK_NUMPAD7):
            s["bg_alpha"] = max(0, s["bg_alpha"] - 10)
        if keys.just_pressed(win32con.VK_NUMPAD9):
            s["bg_alpha"] = min(255, s["bg_alpha"] + 10)

    # -------- сканирование --------

    def scan_targets(self) -> Tuple[List[Tuple[int, int, int, int]], np.ndarray]:
        mx, my = win32api.GetCursorPos()
        mon = {
            "top": my - SQUARE_SIZE // 2,
            "left": mx - SQUARE_SIZE // 2,
            "width": SQUARE_SIZE,
            "height": SQUARE_SIZE,
        }

        img1 = np.array(self.sct.grab(mon))
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGRA2GRAY)
        time.sleep(SCAN_INTERVAL)
        img2 = np.array(self.sct.grab(mon))
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGRA2GRAY)

        diff = cv2.absdiff(gray1, gray2)
        _, thresh = cv2.threshold(diff, THRESHOLD_VAL, 255, cv2.THRESH_BINARY)
        kernel = np.ones((15, 15), dtype=np.uint8)
        closed = cv2.morphologyEx(cv2.dilate(thresh, kernel), cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid = [c for c in contours if MIN_TARGET < cv2.contourArea(c) < MAX_MAP_AREA]

        targets: List[Tuple[int, int, int, int]] = []
        for cnt in valid:
            tx, ty, tw, th = cv2.boundingRect(cnt)
            targets.append((mon["left"] + tx, mon["top"] + ty, tw, th))

        return targets, cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

    # -------- главный цикл --------

    def logic_tick(self) -> None:
        # Скрыть / показать
        if keys.just_pressed(VK_HIDE):
            self.toggle_hide()

        if self.is_hidden:
            return

        # Выход
        if keys.pressed(VK_EXIT):
            sys.exit()

        # Трекинг
        if keys.just_pressed(VK_TRACK):
            shared_data["tracking_mode"] = not shared_data["tracking_mode"]

        # Thermal
        if shared_data.pop("thermal_toggle_request", False):
            self.toggle_thermal()

        if shared_data["thermal_active"]:
            self._handle_thermal_hotkeys()

        # Сканирование
        display_frame = np.zeros((300, 300, 3), dtype=np.uint8)
        if keys.pressed(VK_SCAN):
            shared_data["active"] = True
            try:
                targets, display_frame = self.scan_targets()
                shared_data["targets"] = targets
                if targets:
                    main = max(targets, key=lambda r: r[2] * r[3])
                    shared_data["main_target"] = main
                    if shared_data["tracking_mode"]:
                        self.execute_tracking(main)
            except Exception:
                pass
        else:
            shared_data["active"] = False
            shared_data["targets"] = []
            shared_data["main_target"] = None

        self._draw_radar_status(display_frame)
        cv2.imshow("RADAR_VIEW", display_frame)
        cv2.waitKey(1)

        hwnd = win32gui.FindWindow(None, "RADAR_VIEW")
        if hwnd:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )

        self.update()

    @staticmethod
    def _draw_radar_status(frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        cx, cy = w // 2, h // 2
        line_color = (60, 60, 60)
        cross_color = (0, 180, 0)

        cv2.line(frame, (0, cy), (w, cy), line_color, 1)
        cv2.line(frame, (cx, 0), (cx, h), line_color, 1)
        cv2.circle(frame, (cx, cy), 2, cross_color, -1)

        active_color = (0, 255, 0)
        inactive_color = (128, 128, 128)

        if not shared_data["active"]:
            cv2.putText(frame, "HOLD F TO SCAN", (50, 150), font, 0.6, (255, 255, 255), 1)
            cv2.putText(frame, "[V] Toggle Tracking", (10, h - 10), font, 0.4, inactive_color, 1)
            return

        y = 20
        cv2.putText(frame, "RADAR ACTIVE", (10, y), font, 0.5, active_color, 1)
        y += 20
        track_color = active_color if shared_data["tracking_mode"] else inactive_color
        cv2.putText(frame, f"TRACKING [V]: {'ON' if shared_data['tracking_mode'] else 'OFF'}",
                    (10, y), font, 0.5, track_color, 1)
        y += 20
        thermal_color = (255, 220, 0) if shared_data["thermal_active"] else inactive_color
        cv2.putText(frame, f"THERMAL [Q]: {'ON' if shared_data['thermal_active'] else 'OFF'}",
                    (10, y), font, 0.5, thermal_color, 1)
        y += 20
        tgt_color = active_color if shared_data["targets"] else inactive_color
        cv2.putText(frame, f"TARGETS: {len(shared_data['targets'])}", (10, y), font, 0.5, tgt_color, 1)

    # -------- PaintEvent --------

    def paintEvent(self, event) -> None:
        if not shared_data["active"] or self.is_hidden:
            return

        painter = QPainter(self)
        mx, my = win32api.GetCursorPos()
        painter.setPen(QPen(QColor(0, 255, 0, 80), 1))
        painter.drawEllipse(mx - SQUARE_SIZE // 2, my - SQUARE_SIZE // 2, SQUARE_SIZE, SQUARE_SIZE)

        for rect in shared_data["targets"]:
            rx, ry, rw, rh = rect
            is_main = rect == shared_data["main_target"]
            color = QColor(255, 0, 0) if is_main else QColor(255, 140, 0)
            painter.setPen(QPen(color, 2 if is_main else 1))
            painter.drawRect(rx, ry, rw, rh)

            cx, cy = shared_data["screen_center"]
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 60), 3, Qt.DashLine))
            painter.drawLine(cx, cy, rx + rw // 2, ry + rh // 2)


# ==================== THERMAL OVERLAY ====================

class ThermalOverlay(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        sw, sh = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, sw, sh)
        self._sw, self._sh = sw, sh

        self._scan_timer = QTimer()
        self._scan_timer.timeout.connect(self._scan_tick)
        self._draw_timer = QTimer()
        self._draw_timer.timeout.connect(self.update)

    def start(self) -> None:
        self._scan_timer.start(50)
        self._draw_timer.start(16)

    def stop(self) -> None:
        self._scan_timer.stop()
        self._draw_timer.stop()
        shared_data["thermal_prev_frame"] = None
        shared_data["thermal_diff_points"] = []
        shared_data["thermal_found"] = False

    def _scan_tick(self) -> None:
        if not shared_data["thermal_active"]:
            return

        try:
            gray = capture_roblox_window()
            if gray is None:
                shared_data["thermal_diff_points"] = []
                shared_data["thermal_found"] = False
                return

            shared_data["thermal_found"] = True
            prev = shared_data["thermal_prev_frame"]

            if prev is None or prev.shape != gray.shape:
                shared_data["thermal_prev_frame"] = gray
                return

            diff = cv2.absdiff(prev, gray)
            shared_data["thermal_prev_frame"] = gray

            _, mask = cv2.threshold(diff, THERMAL_SETTINGS["threshold"], 255, cv2.THRESH_BINARY)

            step = max(1, THERMAL_SETTINGS["sample_step"])
            max_dots = THERMAL_SETTINGS["max_dots"]
            sub = mask[::step, ::step]
            ys, xs = np.where(sub == 255)
            ys, xs = ys * step, xs * step

            if len(xs) > max_dots:
                idx = np.random.choice(len(xs), max_dots, replace=False)
                xs, ys = xs[idx], ys[idx]

            ox, oy = shared_data.get("thermal_origin", (0, 0))
            shared_data["thermal_diff_points"] = [
                (int(x) + ox, int(y) + oy) for x, y in zip(xs.tolist(), ys.tolist())
            ]
        except Exception:
            shared_data["thermal_diff_points"] = []

    def paintEvent(self, event) -> None:
        if not shared_data["thermal_active"]:
            return

        painter = QPainter(self)
        painter.fillRect(0, 0, self._sw, self._sh, QColor(0, 0, 0, THERMAL_SETTINGS["bg_alpha"]))

        ds = max(1, THERMAL_SETTINGS["dot_size"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        for x, y in shared_data["thermal_diff_points"]:
            painter.fillRect(x, y, ds, ds, QColor(255, 255, 255, 255))

        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.setFont(QFont("Consolas", 10, QFont.Bold))
        painter.drawText(14, 20, "[ THERMAL ]  ROBLOX MOTION DETECTOR")

        painter.setFont(QFont("Consolas", 8))
        if shared_data.get("thermal_found", False):
            painter.drawText(
                14, 34,
                f"Dots:{len(shared_data['thermal_diff_points'])}  "
                f"THR:{THERMAL_SETTINGS['threshold']}  "
                f"DOT:{ds}px  "
                f"STEP:{THERMAL_SETTINGS['sample_step']}  "
                f"BG:{THERMAL_SETTINGS['bg_alpha']} | Q=close",
            )
        else:
            painter.setPen(QPen(QColor(255, 80, 80, 220), 1))
            painter.drawText(14, 34, "Roblox Player not found! | Q = close")


# ==================== ЗАПУСК ====================

def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> None:
    enable_dpi_awareness()
    app = QApplication(sys.argv)
    overlay = Overlay()
    overlay.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
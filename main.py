#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RADAR Macro для Python 3.11.9 (Локальная версия без проверок)
"""
import sys
import json
from pathlib import Path

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QTextEdit, QMessageBox, QDialog, QCheckBox)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor, QPainter, QPen

# Остальные импорты для основной программы
import cv2
import numpy as np
import mss
import time
import win32api
import win32con
import win32gui
import pydirectinput
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timezone
import threading
from pynput import keyboard

__version__ = "beta-2.3 (local)"

# ==================== КОНФИГ ====================

CONFIG_DIR = Path.home() / ".radar_macro"
CONFIG_DIR.mkdir(exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"


# ==================== ОСНОВНАЯ ПРОГРАММА ====================

def load_config():
    if not CONFIG_FILE.exists():
        if getattr(sys, 'frozen', False):
            bundle_dir = Path(sys.executable).parent
        else:
            bundle_dir = Path(__file__).parent

        default_config_path = bundle_dir / "default_config.json"
        if default_config_path.exists():
            import shutil
            shutil.copy2(default_config_path, CONFIG_FILE)
        else:
            default_settings = {
                "HARPOON_BINDS": ["z", "c", "2", "3", "4", "5"],
                "ROCKET_BINDS": ["x", "v", "7", "8", "9", "0"],
                "NUM_ACTIVE_ROCKETS": 2,
                "SQUARE_SIZE": 400,
                "THRESHOLD_VAL": 7,
                "MIN_TARGET": 0,
                "MAX_MAP_AREA": 10000,
                "SCAN_INTERVAL": 0.05,
                "Q_SETTINGS": {
                    "threshold": 7,
                    "dot_size": 1,
                    "sample_step": 1,
                    "bg_alpha": 140,
                    "max_dots": 20000
                },
                "BINDS": {
                    "key_scan": "F",
                    "key_q_mode": "Q",
                    "key_tracking": "V",
                    "key_exit": "ESCAPE",
                    "key_hide": "Ё"
                },
                "Q_MODE_WINDOWS": ["Roblox", "Roblox Player", "ROBLOX"]
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(default_settings, f, indent=4)

    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


config: Dict[str, Any] = load_config()

HARPOON_BINDS = config["HARPOON_BINDS"]
ROCKET_BINDS = config["ROCKET_BINDS"]
NUM_ACTIVE_ROCKETS = config["NUM_ACTIVE_ROCKETS"]
SQUARE_SIZE = config["SQUARE_SIZE"]
THRESHOLD_VAL = config["THRESHOLD_VAL"]
MIN_TARGET = config["MIN_TARGET"]
MAX_MAP_AREA = config["MAX_MAP_AREA"]
SCAN_INTERVAL = config["SCAN_INTERVAL"]

_binds_cfg = config.get("BINDS", {})


def _resolve_vk(key_name: str) -> int:
    key_name = key_name.upper().strip()
    _special = {
        "ESCAPE": win32con.VK_ESCAPE,
        "ESC":    win32con.VK_ESCAPE,
        "F1": win32con.VK_F1, "F2": win32con.VK_F2, "F3": win32con.VK_F3,
        "F4": win32con.VK_F4, "F5": win32con.VK_F5, "F6": win32con.VK_F6,
        "F7": win32con.VK_F7, "F8": win32con.VK_F8, "F9": win32con.VK_F9,
        "F10": win32con.VK_F10, "F11": win32con.VK_F11, "F12": win32con.VK_F12,
        "SPACE": win32con.VK_SPACE,
        "SHIFT": win32con.VK_SHIFT,
        "CTRL":  win32con.VK_CONTROL,
        "ALT":   win32con.VK_MENU,
        "BACK":  win32con.VK_BACK,
        "TAB":   win32con.VK_TAB,
        "RETURN": win32con.VK_RETURN,
        "ENTER": win32con.VK_RETURN,
        "INSERT": win32con.VK_INSERT,
        "DELETE": win32con.VK_DELETE,
        "HOME": win32con.VK_HOME,
        "END": win32con.VK_END,
        "PGUP": win32con.VK_PRIOR,
        "PGDN": win32con.VK_NEXT,
        "UP": win32con.VK_UP,
        "DOWN": win32con.VK_DOWN,
        "LEFT": win32con.VK_LEFT,
        "RIGHT": win32con.VK_RIGHT,
    }

    if key_name in _special:
        return _special[key_name]
    if key_name == "Ё":
        return 0xBD
    if key_name == "`" or key_name == "~":
        return 0xC0
    if len(key_name) == 1:
        return ord(key_name)
    return ord(key_name[0])


VK_SCAN  = _resolve_vk(_binds_cfg.get("key_scan",     "F"))
VK_QMODE = _resolve_vk(_binds_cfg.get("key_q_mode",   "Q"))
VK_TRACK = _resolve_vk(_binds_cfg.get("key_tracking", "V"))
VK_EXIT  = _resolve_vk(_binds_cfg.get("key_exit",     "ESCAPE"))
VK_HIDE  = _resolve_vk(_binds_cfg.get("key_hide",     "Ё"))

Q_MODE_WINDOWS: List[str] = config.get("Q_MODE_WINDOWS", ["Roblox", "Roblox Player", "ROBLOX"])

pydirectinput.PAUSE = 0.01
pydirectinput.FAILSAFE = False

shared_data: Dict = {
    "active": False,
    "q_key_fired": False,
    "tracking_mode": False,
    "screen_center": (win32api.GetSystemMetrics(0) // 2, win32api.GetSystemMetrics(1) // 2),
    "targets": [],
    "main_target": None,
    "current_rocket_index": 0,
    "v_key_pressed": False,
    "q_mode_active": False,
    "q_key_pressed": False,
    "q_diff_points": [],
    "q_prev_frame": None,
    "q_roblox_origin": (0, 0),
    "q_roblox_found": False,
    "q_roblox_size":  (0, 0),
    "overlay_visible": True,
}

Q_SETTINGS: Dict = {
    "threshold":   7,
    "dot_size":     1,
    "sample_step":  1,
    "bg_alpha":   140,
    "max_dots":  20000,
}
Q_SETTINGS = config["Q_SETTINGS"].copy()

keyboard_listener = None
last_q_press_time = 0


def on_press(key):
    global last_q_press_time
    try:
        if hasattr(key, 'char') and key.char:
            if key.char.lower() == 'q' and not shared_data.get("q_key_fired", False):
                current_time = time.time()
                if current_time - last_q_press_time > 0.3:
                    shared_data["q_key_fired"] = True
                    last_q_press_time = current_time
        elif hasattr(key, 'vk') and key.vk == ord('Q'):
            if not shared_data.get("q_key_fired", False):
                current_time = time.time()
                if current_time - last_q_press_time > 0.3:
                    shared_data["q_key_fired"] = True
                    last_q_press_time = current_time
    except:
        pass


def start_global_keyboard_listener():
    global keyboard_listener
    if keyboard_listener is None or not keyboard_listener.is_alive():
        keyboard_listener = keyboard.Listener(on_press=on_press)
        keyboard_listener.daemon = True
        keyboard_listener.start()


class Overlay(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1))

        self.sct = mss.mss()
        cv2.namedWindow("RADAR_VIEW", cv2.WINDOW_AUTOSIZE)
        self._q_overlay: Optional["QModeOverlay"] = None
        self.is_hidden = False
        self.last_hide_press = 0

        self.timer: QTimer = QTimer()
        self.timer.timeout.connect(self.logic_tick)
        self.timer.start(15)

        start_global_keyboard_listener()

    def toggle_hide(self):
        print(f"Toggle hide called, current state: {self.is_hidden}")
        if not self.is_hidden:
            self.is_hidden = True
            shared_data["active"] = False
            shared_data["tracking_mode"] = False
            shared_data["q_mode_active"] = False
            try:
                cv2.destroyWindow("RADAR_VIEW")
            except:
                pass
            self.hide()
            if self._q_overlay is not None:
                self._q_overlay.stop()
                self._q_overlay.hide()
            print("Program hidden")
        else:
            self.is_hidden = False
            self.show()
            try:
                cv2.namedWindow("RADAR_VIEW", cv2.WINDOW_AUTOSIZE)
                hwnd = win32gui.FindWindow(None, "RADAR_VIEW")
                if hwnd:
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                         win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            except Exception as e:
                print(f"Error showing RADAR_VIEW: {e}")
            print("Program shown")

    def calculate_motion_vector(self, current_rect: Tuple[int, int, int, int], target_id: str = "main") -> Tuple[int, int]:
        x, y, w, h = current_rect
        return x + w // 2, y + h // 2

    def teleport_to_target(self, tx: int, ty: int) -> None:
        win32api.SetCursorPos((tx, ty))

    def execute_tracking(self, target_rect: Tuple[int, int, int, int]) -> None:
        aim_x, aim_y = self.calculate_motion_vector(target_rect)
        try:
            self.teleport_to_target(aim_x, aim_y)
        except Exception:
            pass

    def execute_fire_sequence(self, target_rect: Tuple[int, int, int, int]) -> None:
        aim_x, aim_y = self.calculate_motion_vector(target_rect)
        try:
            pydirectinput.moveTo(aim_x, aim_y)
            time.sleep(0.02)

            rocket_idx = shared_data["current_rocket_index"]
            if rocket_idx >= NUM_ACTIVE_ROCKETS:
                rocket_idx = 0
                shared_data["current_rocket_index"] = 0

            pydirectinput.press(HARPOON_BINDS[rocket_idx])
            time.sleep(0.02)
            pydirectinput.mouseDown(button='left')
            time.sleep(0.1)
            pydirectinput.mouseUp(button='left')
            time.sleep(0.02)
            pydirectinput.press(ROCKET_BINDS[rocket_idx])

            shared_data["current_rocket_index"] = (rocket_idx + 1) % NUM_ACTIVE_ROCKETS
        except Exception:
            pass

    def logic_tick(self) -> None:
        hide_pressed = False

        if win32api.GetAsyncKeyState(VK_HIDE) & 0x8000:
            hide_pressed = True

        try:
            if win32api.GetAsyncKeyState(0xBD) & 0x8000:
                hide_pressed = True
        except:
            pass

        try:
            alt_codes = [0xBD, 0xDE, 0xC0, 0xBC]
            for code in alt_codes:
                if win32api.GetAsyncKeyState(code) & 0x8000:
                    hide_pressed = True
                    break
        except:
            pass

        if hide_pressed:
            current_time = time.time()
            if current_time - self.last_hide_press > 0.3:
                self.last_hide_press = current_time
                self.toggle_hide()
                return

        if self.is_hidden:
            return

        display_frame = np.zeros((300, 300, 3), dtype=np.uint8)

        if win32api.GetAsyncKeyState(VK_EXIT) & 0x8000:
            sys.exit()

        v_key_state = bool(win32api.GetAsyncKeyState(VK_TRACK) & 0x8000)
        if v_key_state and not shared_data["v_key_pressed"]:
            shared_data["tracking_mode"] = not shared_data["tracking_mode"]
            shared_data["v_key_pressed"] = True
        elif not v_key_state:
            shared_data["v_key_pressed"] = False

        if shared_data.get("q_key_fired"):
            shared_data["q_key_fired"] = False
            if not shared_data["q_mode_active"]:
                shared_data["q_mode_active"] = True
                if self._q_overlay is None:
                    self._q_overlay = QModeOverlay()
                self._q_overlay.start()
                self._q_overlay.show()
            else:
                shared_data["q_mode_active"] = False
                if self._q_overlay is not None:
                    self._q_overlay.stop()
                    self._q_overlay.hide()

        q_key_state = bool(win32api.GetAsyncKeyState(VK_QMODE) & 0x8000)
        if q_key_state and not shared_data["q_key_pressed"]:
            if not shared_data["q_mode_active"]:
                shared_data["q_mode_active"] = True
                if self._q_overlay is None:
                    self._q_overlay = QModeOverlay()
                self._q_overlay.start()
                self._q_overlay.show()
            else:
                shared_data["q_mode_active"] = False
                if self._q_overlay is not None:
                    self._q_overlay.stop()
                    self._q_overlay.hide()
            shared_data["q_key_pressed"] = True
        elif not q_key_state:
            shared_data["q_key_pressed"] = False

        if shared_data["q_mode_active"]:
            if win32api.GetAsyncKeyState(win32con.VK_ADD) & 1:
                Q_SETTINGS["threshold"] = min(80, Q_SETTINGS["threshold"] + 1)
            if win32api.GetAsyncKeyState(win32con.VK_SUBTRACT) & 1:
                Q_SETTINGS["threshold"] = max(1, Q_SETTINGS["threshold"] - 1)
            if win32api.GetAsyncKeyState(win32con.VK_NUMPAD8) & 1:
                Q_SETTINGS["dot_size"] = min(8, Q_SETTINGS["dot_size"] + 1)
            if win32api.GetAsyncKeyState(win32con.VK_NUMPAD2) & 1:
                Q_SETTINGS["dot_size"] = max(1, Q_SETTINGS["dot_size"] - 1)
            if win32api.GetAsyncKeyState(win32con.VK_NUMPAD4) & 1:
                Q_SETTINGS["sample_step"] = max(1, Q_SETTINGS["sample_step"] - 1)
            if win32api.GetAsyncKeyState(win32con.VK_NUMPAD6) & 1:
                Q_SETTINGS["sample_step"] = min(12, Q_SETTINGS["sample_step"] + 1)
            if win32api.GetAsyncKeyState(win32con.VK_NUMPAD7) & 1:
                Q_SETTINGS["bg_alpha"] = max(0, Q_SETTINGS["bg_alpha"] - 10)
            if win32api.GetAsyncKeyState(win32con.VK_NUMPAD9) & 1:
                Q_SETTINGS["bg_alpha"] = min(255, Q_SETTINGS["bg_alpha"] + 10)

        is_f_pressed = bool(win32api.GetAsyncKeyState(VK_SCAN) & 0x8000)
        if is_f_pressed:
            shared_data["active"] = True
            try:
                mx, my = win32api.GetCursorPos()
                mon = {"top": my - SQUARE_SIZE // 2, "left": mx - SQUARE_SIZE // 2, "width": SQUARE_SIZE, "height": SQUARE_SIZE}
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
                display_frame = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
                valid_contours = [c for c in contours if MIN_TARGET < cv2.contourArea(c) < MAX_MAP_AREA]
                current_targets = []
                for cnt in valid_contours:
                    tx, ty, tw, th = cv2.boundingRect(cnt)
                    current_targets.append((mon['left'] + tx, mon['top'] + ty, tw, th))
                shared_data["targets"] = current_targets
                if current_targets:
                    main = max(current_targets, key=lambda r: r[2] * r[3])
                    shared_data["main_target"] = main
                    if shared_data["tracking_mode"]:
                        self.execute_tracking(main)
                if win32api.GetAsyncKeyState(0x5A) & 1:
                    if shared_data["main_target"]:
                        self.execute_fire_sequence(shared_data["main_target"])
            except:
                pass
        else:
            shared_data["active"] = False
            shared_data["targets"] = []
            shared_data["main_target"] = None

        self.draw_radar_status(display_frame)
        cv2.imshow("RADAR_VIEW", display_frame)
        cv2.waitKey(1)
        hwnd = win32gui.FindWindow(None, "RADAR_VIEW")
        if hwnd:
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        self.update()

    def draw_radar_status(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        cx, cy = w // 2, h // 2
        line_color = (60, 60, 60)
        cross_color = (0, 180, 0)
        cv2.line(frame, (0, cy), (w, cy), line_color, 1)
        cv2.line(frame, (cx, 0), (cx, h), line_color, 1)
        cv2.circle(frame, (cx, cy), 2, cross_color, -1)
        y_offset = 20
        color_active, color_inactive = (0, 255, 0), (128, 128, 128)

        if not shared_data["active"]:
            cv2.putText(frame, "HOLD F TO SCAN", (50, 150), font, 0.6, (255, 255, 255), 1)
            cv2.putText(frame, "[V] Toggle Tracking", (10, h - 10), font, 0.4, color_inactive, 1)
            return

        cv2.putText(frame, "RADAR ACTIVE", (10, y_offset), font, 0.5, color_active, 1)
        y_offset += 20
        cv2.putText(frame, f"TRACKING [V]: {'ON' if shared_data['tracking_mode'] else 'OFF'}", (10, y_offset), font, 0.5, color_active if shared_data["tracking_mode"] else color_inactive, 1)
        y_offset += 20
        cv2.putText(frame, f"FULLSCAN [Q]: {'ON' if shared_data['q_mode_active'] else 'OFF'}", (10, y_offset), font, 0.5, (255, 220, 0) if shared_data["q_mode_active"] else color_inactive, 1)
        y_offset += 20
        cv2.putText(frame, f"TARGETS: {len(shared_data['targets'])}", (10, y_offset), font, 0.5, color_active if shared_data["targets"] else color_inactive, 1)

    def paintEvent(self, event) -> None:
        if not shared_data["active"] or self.is_hidden:
            return
        painter = QPainter(self)
        mx, my = win32api.GetCursorPos()
        painter.setPen(QPen(QColor(0, 255, 0, 80), 1))
        painter.drawEllipse(mx - SQUARE_SIZE // 2, my - SQUARE_SIZE // 2, SQUARE_SIZE, SQUARE_SIZE)
        for rect in shared_data["targets"]:
            rx, ry, rw, rh = rect
            is_main = (rect == shared_data["main_target"])
            color = QColor(255, 0, 0) if is_main else QColor(255, 140, 0)
            painter.setPen(QPen(color, 2 if is_main else 1))
            painter.drawRect(rx, ry, rw, rh)
            cx, cy = shared_data["screen_center"]
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 60), 3, Qt.DashLine))
            painter.drawLine(cx, cy, rx + rw // 2, ry + rh // 2)


def _find_roblox_hwnd() -> Optional[int]:
    for title in Q_MODE_WINDOWS:
        hwnd = win32gui.FindWindow(None, title)
        if hwnd:
            return hwnd
    return None


def _capture_roblox_window() -> Optional[np.ndarray]:
    import ctypes
    roblox_hwnd = _find_roblox_hwnd()
    if not roblox_hwnd:
        return None
    cl, ct, cr, cb = win32gui.GetClientRect(roblox_hwnd)
    w, h = cr - cl, cb - ct
    if w <= 0 or h <= 0:
        return None
    ox, oy = win32gui.ClientToScreen(roblox_hwnd, (0, 0))
    shared_data["q_roblox_origin"] = (ox, oy)
    shared_data["q_roblox_size"] = (w, h)
    hwnd_dc = win32gui.GetDC(roblox_hwnd)
    mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    ctypes.windll.gdi32.SelectObject(mem_dc, bmp)
    if not ctypes.windll.user32.PrintWindow(roblox_hwnd, mem_dc, 0x05):
        ctypes.windll.gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, 0x00CC0020)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

    bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=24, biCompression=0)
    buf = (ctypes.c_byte * (w * h * 3))()
    ctypes.windll.gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    ctypes.windll.gdi32.DeleteObject(bmp)
    ctypes.windll.gdi32.DeleteDC(mem_dc)
    win32gui.ReleaseDC(roblox_hwnd, hwnd_dc)
    img = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


class QModeOverlay(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        sw, sh = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, sw, sh)
        self._sw, self._sh = sw, sh
        self._scan_timer = QTimer()
        self._scan_timer.timeout.connect(self._scan_tick)
        self._draw_timer = QTimer()
        self._draw_timer.timeout.connect(self.update)

    def start(self):
        self._scan_timer.start(50)
        self._draw_timer.start(16)

    def stop(self) -> None:
        self._scan_timer.stop()
        self._draw_timer.stop()
        shared_data["q_prev_frame"] = None
        shared_data["q_diff_points"] = []
        shared_data["q_roblox_found"] = False

    def _scan_tick(self) -> None:
        if not shared_data["q_mode_active"]:
            return
        try:
            gray = _capture_roblox_window()
            if gray is None:
                shared_data["q_diff_points"], shared_data["q_roblox_found"] = [], False
                return
            shared_data["q_roblox_found"] = True
            prev = shared_data["q_prev_frame"]
            if prev is None or prev.shape != gray.shape:
                shared_data["q_prev_frame"] = gray
                return
            diff = cv2.absdiff(prev, gray)
            shared_data["q_prev_frame"] = gray
            _, mask = cv2.threshold(diff, Q_SETTINGS["threshold"], 255, cv2.THRESH_BINARY)
            step, mxd = max(1, Q_SETTINGS["sample_step"]), Q_SETTINGS["max_dots"]
            sub = mask[::step, ::step]
            ys, xs = np.where(sub == 255)
            ys, xs = ys * step, xs * step
            if len(xs) > mxd:
                idx = np.random.choice(len(xs), mxd, replace=False)
                xs, ys = xs[idx], ys[idx]
            ox, oy = shared_data.get("q_roblox_origin", (0, 0))
            shared_data["q_diff_points"] = [(int(x) + ox, int(y) + oy) for x, y in zip(xs.tolist(), ys.tolist())]
        except:
            shared_data["q_diff_points"] = []

    def paintEvent(self, event) -> None:
        if not shared_data["q_mode_active"]:
            return
        painter = QPainter(self)
        painter.fillRect(0, 0, self._sw, self._sh, QColor(0, 0, 0, Q_SETTINGS["bg_alpha"]))
        ds = max(1, Q_SETTINGS["dot_size"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        for x, y in shared_data["q_diff_points"]:
            painter.fillRect(x, y, ds, ds, QColor(255, 255, 255, 255))
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.setFont(QFont("Consolas", 10, QFont.Bold))
        painter.drawText(14, 20, "[ Q-SCAN ]  ROBLOX MOTION DETECTOR")
        painter.setFont(QFont("Consolas", 8))
        if shared_data.get("q_roblox_found", False):
            painter.drawText(14, 34, f"Dots:{len(shared_data['q_diff_points'])}  THR:{Q_SETTINGS['threshold']}  DOT:{ds}px  STEP:{Q_SETTINGS['sample_step']}  BG:{Q_SETTINGS['bg_alpha']} | Q=close")
        else:
            painter.setPen(QPen(QColor(255, 80, 80, 220), 1))
            painter.drawText(14, 34, "Roblox Player not found! | Q = close")


# ==================== ЗАПУСК ====================

def main() -> None:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except:
            pass

    app = QApplication(sys.argv)

    # ===== ЗАПУСК БЕЗ ПРОВЕРОК =====
    overlay = Overlay()
    overlay.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
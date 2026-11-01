#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Графическая настройка биндов и параметров RADAR Macro."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

CONFIG_DIR = Path.home() / ".radar_macro"
CONFIG_FILE = CONFIG_DIR / "config.json"


class BindSpec(NamedTuple):
    """Одно действие, которое можно назначить на клавишу."""

    key: str
    title: str
    default: str
    note: str


BIND_SPECS: Tuple[BindSpec, ...] = (
    BindSpec("key_scan", "Радар / сканирование", "F", "удерживать"),
    BindSpec("key_thermal", "Тепловизор", "Q", "вкл / выкл"),
    BindSpec("key_fullscan", "Полный поиск", "H", "вкл / выкл"),
    BindSpec("key_thermal_mode", "Режим тепловизора", "Y", "окно / оверлей"),
    BindSpec("key_tracking", "Слежение за целью", "V", "вкл / выкл"),
    BindSpec("key_profile", "Профиль поиска", "G", "precision / noise"),
    BindSpec("key_zoom", "Увеличение RADAR_VIEW", "Z", "x2"),
    BindSpec("key_zoom_hold", "TV-линза", "RMB", "удерживать; можно мышь"),
    BindSpec("key_capture", "Захват / смена цели", "T", "LOCK"),
    BindSpec("key_lock_mode", "Размер хардлока", "X", "NORMAL / FAR / NEAR"),
    BindSpec("key_center_hold", "Удержание в центре", "N", "клавиши корректировки"),
    BindSpec("key_exit", "Выход", "ESCAPE", "завершение"),
    BindSpec("key_hide", "Скрыть / показать", "Ё", "свернуть"),
)

DEFAULT_BINDS: Dict[str, str] = {spec.key: spec.default for spec in BIND_SPECS}
BIND_BY_KEY: Dict[str, BindSpec] = {spec.key: spec for spec in BIND_SPECS}

CENTER_KEY_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("CENTER_KEY_LEFT", "Влево", "A"),
    ("CENTER_KEY_RIGHT", "Вправо", "D"),
    ("CENTER_KEY_UP", "Вверх", "W"),
    ("CENTER_KEY_DOWN", "Вниз", "S"),
)
DEFAULT_CENTER_KEYS: Dict[str, str] = {
    key: default for key, _label, default in CENTER_KEY_SPECS
}

DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
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
}

DEFAULT_LOCK: Dict[str, Any] = {
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
}

DEFAULT_THERMAL: Dict[str, Any] = {
    "threshold": 7,
    "dot_size": 1,
    "sample_step": 1,
    "bg_alpha": 140,
    "max_dots": 20000,
}

DEFAULT_THERMAL_WINDOWS: List[str] = ["Roblox", "Roblox Player", "ROBLOX"]

DEFAULT_CONFIG: Dict[str, Any] = {
    "CONFIG_VERSION": 4,
    "SQUARE_SIZE": 400,
    "SCAN_INTERVAL": 0.05,
    "SHOW_RADAR_VIEW": True,
    "LOCK_SIZE_MODE": "normal",
    "INTERFACE_MODE": "new",
    "CENTER_USE_KEYS": True,
    **DEFAULT_CENTER_KEYS,
    "CENTER_HOLD_SECONDS": 1.0,
    "CENTER_REST_SECONDS": 0.15,
    "CENTER_AUTO_RMB": False,
    "MOTION_MAP_GAIN": 8,
    "ACTIVE_PROFILE": "precision",
    "SEARCH_PROFILES": copy.deepcopy(DEFAULT_PROFILES),
    "LOCK_SETTINGS": copy.deepcopy(DEFAULT_LOCK),
    "THERMAL_SETTINGS": copy.deepcopy(DEFAULT_THERMAL),
    "THERMAL_MODE": "window",
    "BINDS": dict(DEFAULT_BINDS),
    "THERMAL_WINDOWS": list(DEFAULT_THERMAL_WINDOWS),
}

_DISPLAY_NAMES: Dict[str, str] = {
    "ESCAPE": "ESC",
    "RETURN": "ENTER",
    "BACK": "BACKSPACE",
    "PGUP": "PAGE UP",
    "PGDN": "PAGE DOWN",
}

_IDENTITY_ALIASES: Dict[str, str] = {
    "ESC": "ESCAPE",
    "ENTER": "RETURN",
    "MOUSE1": "LMB",
    "MOUSE2": "RMB",
    "MOUSE3": "MMB",
    "MOUSE4": "XBUTTON1",
    "MOUSE5": "XBUTTON2",
    "`": "Ё",
    "~": "Ё",
}

_SPECIAL_KEYS: Dict[int, str] = {
    Qt.Key_Escape: "ESCAPE",
    Qt.Key_Tab: "TAB",
    Qt.Key_Backtab: "TAB",
    Qt.Key_Backspace: "BACK",
    Qt.Key_Return: "RETURN",
    Qt.Key_Enter: "ENTER",
    Qt.Key_Insert: "INSERT",
    Qt.Key_Delete: "DELETE",
    Qt.Key_Home: "HOME",
    Qt.Key_End: "END",
    Qt.Key_PageUp: "PGUP",
    Qt.Key_PageDown: "PGDN",
    Qt.Key_Up: "UP",
    Qt.Key_Down: "DOWN",
    Qt.Key_Left: "LEFT",
    Qt.Key_Right: "RIGHT",
    Qt.Key_Space: "SPACE",
    Qt.Key_F1: "F1",
    Qt.Key_F2: "F2",
    Qt.Key_F3: "F3",
    Qt.Key_F4: "F4",
    Qt.Key_F5: "F5",
    Qt.Key_F6: "F6",
    Qt.Key_F7: "F7",
    Qt.Key_F8: "F8",
    Qt.Key_F9: "F9",
    Qt.Key_F10: "F10",
    Qt.Key_F11: "F11",
    Qt.Key_F12: "F12",
}

_KEYPAD_KEYS: Dict[int, str] = {
    Qt.Key_0: "NUMPAD0",
    Qt.Key_1: "NUMPAD1",
    Qt.Key_2: "NUMPAD2",
    Qt.Key_3: "NUMPAD3",
    Qt.Key_4: "NUMPAD4",
    Qt.Key_5: "NUMPAD5",
    Qt.Key_6: "NUMPAD6",
    Qt.Key_7: "NUMPAD7",
    Qt.Key_8: "NUMPAD8",
    Qt.Key_9: "NUMPAD9",
    Qt.Key_Plus: "ADD",
    Qt.Key_Minus: "SUBTRACT",
    Qt.Key_Asterisk: "MULTIPLY",
    Qt.Key_Slash: "DIVIDE",
    Qt.Key_Period: "DECIMAL",
    Qt.Key_Comma: "DECIMAL",
    Qt.Key_Enter: "ENTER",
}

_MOUSE_BUTTONS: Dict[int, str] = {
    Qt.LeftButton: "LMB",
    Qt.RightButton: "RMB",
    Qt.MiddleButton: "MMB",
    Qt.XButton1: "XBUTTON1",
    Qt.XButton2: "XBUTTON2",
}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    return default


def _normalize_profiles(raw: Any) -> Dict[str, Dict[str, Any]]:
    profiles: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        for name, profile in raw.items():
            source = str(name)
            base = copy.deepcopy(
                DEFAULT_PROFILES.get(source, DEFAULT_PROFILES["precision"])
            )
            if isinstance(profile, dict):
                base.update(profile)
            profiles[source] = base
    if not profiles:
        profiles = copy.deepcopy(DEFAULT_PROFILES)
    return profiles


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Дополняет конфиг дефолтами, сохраняя неизвестные ключи."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in raw.items():
        if key == "BINDS":
            if isinstance(value, dict):
                config["BINDS"].update(value)
        elif key == "SEARCH_PROFILES":
            config["SEARCH_PROFILES"] = _normalize_profiles(value)
        elif key in ("LOCK_SETTINGS", "THERMAL_SETTINGS"):
            if isinstance(value, dict):
                config[key].update(value)
        elif key == "THERMAL_WINDOWS":
            if isinstance(value, list):
                windows = [str(item).strip() for item in value if str(item).strip()]
                if windows:
                    config[key] = windows
        else:
            config[key] = value

    profiles = config["SEARCH_PROFILES"]
    if config.get("ACTIVE_PROFILE") not in profiles:
        config["ACTIVE_PROFILE"] = next(iter(profiles))
    return config


def read_config(path: Optional[Path] = None) -> Tuple[Dict[str, Any], Optional[str]]:
    """Читает config.json и возвращает данные вместе с текстом ошибки."""
    config_path = path or CONFIG_FILE
    if not config_path.exists():
        return {}, None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return {}, str(exc)
    if not isinstance(data, dict):
        return {}, "корень config.json должен быть объектом"
    return data, None


def write_config(path: Path, data: Dict[str, Any]) -> None:
    """Атомарно записывает конфиг."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def load_bind_values(config_data: Dict[str, Any]) -> Dict[str, str]:
    """Достаёт текущие бинды и подставляет дефолты для отсутствующих."""
    raw_binds = config_data.get("BINDS")
    binds = raw_binds if isinstance(raw_binds, dict) else {}
    values: Dict[str, str] = {}
    for spec in BIND_SPECS:
        value = binds.get(spec.key, spec.default)
        if not isinstance(value, str) or not value.strip():
            value = spec.default
        values[spec.key] = value.strip()
    return values


def load_center_values(config_data: Dict[str, Any]) -> Dict[str, str]:
    """Достаёт клавиши корректировки, по умолчанию A/D/W/S."""
    values: Dict[str, str] = {}
    for key, _label, default in CENTER_KEY_SPECS:
        value = config_data.get(key, default)
        if not isinstance(value, str) or not value.strip():
            value = default
        values[key] = value.strip()
    return values


def display_key(value: str) -> str:
    """Короткое имя клавиши для кнопки."""
    name = str(value).strip().upper()
    return _DISPLAY_NAMES.get(name, name)


def bind_identity(value: str) -> str:
    """Каноническое имя для проверки одинаковых клавиш."""
    name = str(value).strip().upper()
    return _IDENTITY_ALIASES.get(name, name)


def modifier_name(key: int) -> Optional[str]:
    """Имя одиночного модификатора."""
    if key == Qt.Key_Shift:
        return "SHIFT"
    if key == Qt.Key_Control:
        return "CTRL"
    if key == Qt.Key_Alt:
        return "ALT"
    return None


def key_name_from_values(key: int, text: str, modifiers: int) -> Optional[str]:
    """Преобразует Qt-событие в имя клавиши, понятное RADAR Macro."""
    modifier = modifier_name(key)
    if modifier is not None:
        return modifier

    if modifiers & Qt.KeypadModifier:
        keypad_name = _KEYPAD_KEYS.get(key)
        if keypad_name is not None:
            return keypad_name

    special = _SPECIAL_KEYS.get(key)
    if special is not None:
        return special

    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key)
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)

    if not text:
        return None
    if len(text) != 1 or not text.isprintable() or text in ("\r", "\n", "\t"):
        return None

    upper = text.upper()
    if len(upper) == 1:
        return upper
    return text


class KeyCaptureDialog(QDialog):
    """Ожидает следующую клавишу или кнопку мыши."""

    def __init__(self, action_title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key_name: Optional[str] = None
        self._pending_modifier: Optional[str] = None
        self._capturing = False
        self._app = QApplication.instance()

        self.setWindowTitle("Назначение клавиши")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumSize(460, 230)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        action_label = QLabel(f"Действие: {action_title}")
        action_label.setWordWrap(True)
        layout.addWidget(action_label)

        self.prompt = QLabel("Нажмите клавишу или кнопку мыши")
        self.prompt.setAlignment(Qt.AlignCenter)
        self.prompt.setMinimumHeight(82)
        self.prompt.setStyleSheet(
            "font-size: 17px; font-weight: 600; "
            "border: 1px solid #777; border-radius: 6px; padding: 12px;"
        )
        layout.addWidget(self.prompt)

        self.hint = QLabel(
            "Esc тоже можно назначить. Для отмены нажмите кнопку «Отмена»."
        )
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setFixedWidth(120)
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        QTimer.singleShot(0, self._begin_capture)

    def _begin_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        self.grabKeyboard()
        if self._app is not None:
            self._app.installEventFilter(self)

    def _stop_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        if self._app is not None:
            self._app.removeEventFilter(self)
        self.releaseKeyboard()

    def _finish(self, name: str) -> None:
        self.key_name = name
        self._stop_capture()
        self.accept()

    def reject(self) -> None:
        self._stop_capture()
        super().reject()

    def closeEvent(self, event: QEvent) -> None:
        self._stop_capture()
        super().closeEvent(event)

    def _is_cancel_target(self, obj: object) -> bool:
        if obj is self.cancel_button:
            return True
        return isinstance(obj, QWidget) and self.cancel_button.isAncestorOf(obj)

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if not self._capturing:
            return super().eventFilter(obj, event)

        event_type = event.type()
        if event_type == QEvent.KeyPress:
            key = event.key()
            modifier = modifier_name(key)
            if modifier is not None:
                self._pending_modifier = modifier
                self.hint.setText("Отпустите модификатор или нажмите другую клавишу.")
                return True

            self._pending_modifier = None
            name = key_name_from_values(
                key, event.text(), int(event.modifiers())
            )
            if name is not None:
                self._finish(name)
            else:
                self.prompt.setText("Эта клавиша не поддерживается")
            return True

        if event_type == QEvent.KeyRelease:
            if self._pending_modifier is not None:
                modifier = modifier_name(event.key())
                if modifier == self._pending_modifier:
                    self._finish(modifier)
            return True

        if event_type == QEvent.MouseButtonPress:
            if self._is_cancel_target(obj):
                return False
            name = _MOUSE_BUTTONS.get(event.button())
            if name is not None:
                self._finish(name)
            return True

        return super().eventFilter(obj, event)


class BindSettingsWindow(QMainWindow):
    """Окно биндов, корректировки и параметров конфига."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RADAR Macro — настройка")
        self.setMinimumSize(780, 620)
        self.resize(860, 760)

        config_data, read_error = read_config()
        self._config = normalize_config(config_data)
        self._values = load_bind_values(self._config)
        self._center_values = load_center_values(self._config)
        self._profiles = copy.deepcopy(self._config["SEARCH_PROFILES"])
        active = str(self._config.get("ACTIVE_PROFILE", ""))
        self._current_profile = (
            active if active in self._profiles else next(iter(self._profiles))
        )
        self._read_error = read_error

        self._loading_profile = False
        self._top_widgets: Dict[str, QWidget] = {}
        self._lock_widgets: Dict[str, QWidget] = {}
        self._thermal_widgets: Dict[str, QWidget] = {}
        self._profile_widgets: Dict[str, QWidget] = {}
        self._buttons: Dict[str, QPushButton] = {}
        self._center_buttons: Dict[str, QPushButton] = {}

        self._build_ui()
        self._initial_config = self._collect_config()
        self._connect_updates()
        self._update_status()
        if read_error is not None:
            QTimer.singleShot(0, self._show_read_error)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel("Настройка RADAR Macro")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        description = QLabel(
            "Бинды, клавиши корректировки и параметры config.json. "
            "Для назначения нажмите на клавишу, затем нажмите нужную клавишу "
            "или кнопку мыши. Поддерживаются LMB, RMB, MMB, XBUTTON1, XBUTTON2."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.tabs = QTabWidget(central)
        layout.addWidget(self.tabs, 1)
        self._build_binds_tab()
        self._build_correction_tab()
        self._build_general_tab()
        self._build_search_tab()
        self._build_lock_tab()
        self._build_thermal_tab()

        warning = QLabel(
            "Изменения вступят в силу после перезапуска RADAR Macro. "
            "Перед настройкой закройте макрос, иначе старые бинды продолжат "
            "срабатывать при нажатии клавиш."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #9a5b00;")
        layout.addWidget(warning)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        path_label = QLabel(f"Конфиг: {CONFIG_FILE}")
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("color: #666;")
        layout.addWidget(path_label)

        button_row = QHBoxLayout()
        reset_button = QPushButton("Сбросить бинды")
        reset_button.clicked.connect(self._reset_binds)
        save_button = QPushButton("Сохранить")
        save_button.setDefault(True)
        save_button.clicked.connect(self._save_all)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.close)
        button_row.addWidget(reset_button)
        button_row.addStretch()
        button_row.addWidget(save_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _new_tab(self, title: str, scroll: bool = True) -> QVBoxLayout:
        if not scroll:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(14, 12, 14, 14)
            layout.setSpacing(10)
            self.tabs.addTab(page, title)
            return layout

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        area.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        self.tabs.addTab(area, title)
        return layout

    @staticmethod
    def _new_group(layout: QVBoxLayout, title: str) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(box)
        return form

    def _build_binds_tab(self) -> None:
        layout = self._new_tab("Бинды", scroll=False)
        note = QLabel(
            "Нажмите на кнопку с текущей клавишей, затем нажмите нужную "
            "клавишу или кнопку мыши."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(len(BIND_SPECS), 3)
        self.table.setHorizontalHeaderLabels(("Действие", "Клавиша", "Примечание"))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 142)
        layout.addWidget(self.table, 1)

        for row, spec in enumerate(BIND_SPECS):
            action_item = QTableWidgetItem(spec.title)
            action_item.setFlags(Qt.ItemIsEnabled)
            note_item = QTableWidgetItem(spec.note)
            note_item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, action_item)
            self.table.setItem(row, 2, note_item)

            button = QPushButton(display_key(self._values[spec.key]))
            button.setFixedWidth(130)
            button.setToolTip("Нажмите, затем нажмите нужную клавишу или кнопку мыши")
            button.clicked.connect(
                lambda checked=False, key=spec.key: self._capture_bind(key)
            )
            self._buttons[spec.key] = button
            self.table.setCellWidget(row, 1, button)

    def _build_correction_tab(self) -> None:
        layout = self._new_tab("Корректировка")

        intro = QLabel(
            "Корректировка удерживает цель в центре при включённом N. "
            "По умолчанию используются A / D / W / S."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        center_check = QCheckBox(
            "Использовать клавиши для корректировки вместо движения мышью"
        )
        center_check.setChecked(_as_bool(self._config.get("CENTER_USE_KEYS"), True))
        self._top_widgets["CENTER_USE_KEYS"] = center_check
        layout.addWidget(center_check)

        keys_form = self._new_group(layout, "Клавиши корректировки")
        for key, label, _default in CENTER_KEY_SPECS:
            button = QPushButton(display_key(self._center_values[key]))
            button.setFixedWidth(130)
            button.setToolTip("Нажмите, затем нажмите нужную клавишу или кнопку мыши")
            button.clicked.connect(
                lambda checked=False, name=key: self._capture_center_key(name)
            )
            self._center_buttons[key] = button
            keys_form.addRow(label, button)

        timing_form = self._new_group(layout, "Тайминги")
        self._make_float(
            timing_form, self._top_widgets, "CENTER_HOLD_SECONDS",
            "Удерживать клавиши", self._config.get("CENTER_HOLD_SECONDS", 1.0),
            0.1, 1.0, 0.05, 2, " сек",
        )
        self._make_float(
            timing_form, self._top_widgets, "CENTER_REST_SECONDS",
            "Пауза между сериями", self._config.get("CENTER_REST_SECONDS", 0.15),
            0.0, 2.0, 0.05, 2, " сек",
        )
        auto_rmb = QCheckBox("Зажимать RMB (старый режим Roblox)")
        auto_rmb.setChecked(_as_bool(self._config.get("CENTER_AUTO_RMB"), False))
        self._top_widgets["CENTER_AUTO_RMB"] = auto_rmb
        timing_form.addRow("", auto_rmb)

        layout.addStretch(1)

    def _build_general_tab(self) -> None:
        layout = self._new_tab("Основные")

        scan_form = self._new_group(layout, "Сканирование")
        self._make_int(
            scan_form, self._top_widgets, "SQUARE_SIZE",
            "Размер квадрата", self._config.get("SQUARE_SIZE", 400),
            64, 2000, 16, " px",
        )
        self._make_float(
            scan_form, self._top_widgets, "SCAN_INTERVAL",
            "Интервал", self._config.get("SCAN_INTERVAL", 0.05),
            0.005, 0.5, 0.005, 3, " сек",
        )
        show_view = QCheckBox("Показывать окно RADAR_VIEW")
        show_view.setChecked(_as_bool(self._config.get("SHOW_RADAR_VIEW"), True))
        self._top_widgets["SHOW_RADAR_VIEW"] = show_view
        scan_form.addRow("", show_view)
        self._make_int(
            scan_form, self._top_widgets, "MOTION_MAP_GAIN",
            "Усиление motion map", self._config.get("MOTION_MAP_GAIN", 8),
            1, 32, 1,
        )

        modes_form = self._new_group(layout, "Режимы")
        self._make_combo(
            modes_form, self._top_widgets, "INTERFACE_MODE",
            "Интерфейс",
            (
                ("new", "NEW — TV-интерфейс"),
                ("normal", "NORMAL — обычный курсор"),
            ),
            str(self._config.get("INTERFACE_MODE", "new")),
        )
        self._make_combo(
            modes_form, self._top_widgets, "LOCK_SIZE_MODE",
            "Размер хардлока",
            (
                ("normal", "NORMAL"),
                ("far", "FAR — меньше"),
                ("near", "NEAR — больше"),
            ),
            str(self._config.get("LOCK_SIZE_MODE", "normal")),
        )
        self._make_combo(
            modes_form, self._top_widgets, "THERMAL_MODE",
            "Тепловизор",
            (
                ("window", "Окно RADAR_VIEW"),
                ("overlay", "Оверлей"),
            ),
            str(self._config.get("THERMAL_MODE", "window")),
        )
        profile_choices = []
        for name, profile in self._profiles.items():
            title = str(profile.get("title", name)).strip()
            profile_choices.append((name, f"{name} — {title}" if title else name))
        self._make_combo(
            modes_form, self._top_widgets, "ACTIVE_PROFILE",
            "Активный профиль", profile_choices,
            str(self._config.get("ACTIVE_PROFILE", self._current_profile)),
        )

        layout.addStretch(1)

    def _build_search_tab(self) -> None:
        layout = self._new_tab("Поиск")

        selector = QHBoxLayout()
        selector.addWidget(QLabel("Профиль:"))
        self._profile_combo = QComboBox()
        self._profile_combo.addItems(list(self._profiles))
        self._profile_combo.setCurrentText(self._current_profile)
        selector.addWidget(self._profile_combo)
        selector.addStretch(1)
        layout.addLayout(selector)

        profile = self._profiles[self._current_profile]
        form = self._new_group(layout, "Параметры профиля")
        self._profile_title = QLineEdit(str(profile.get("title", "")))
        self._profile_title.setPlaceholderText(self._current_profile.upper())
        form.addRow("Название", self._profile_title)
        self._profile_adaptive = QCheckBox("Адаптивный порог")
        self._profile_adaptive.setChecked(_as_bool(profile.get("adaptive"), False))
        form.addRow("", self._profile_adaptive)

        for key, label, low, high, step in (
            ("threshold", "Порог", 1, 255, 1),
            ("dilate", "Дилатация", 1, 51, 2),
            ("blur", "Размытие", 0, 21, 2),
            ("open", "Открытие", 0, 15, 2),
            ("min_seed", "Мин. seed", 0, 100000, 1),
            ("min_area", "Мин. площадь", 0, 100000, 1),
            ("max_area", "Макс. площадь", 1, 1000000, 100),
            ("min_hits", "Мин. попаданий", 1, 15, 1),
            ("max_misses", "Макс. промахов", 0, 10, 1),
            ("match_dist", "Дистанция совпадения", 0, 1000, 1),
        ):
            self._make_int(
                form, self._profile_widgets, key, label, profile.get(key),
                low, high, step,
            )

        for key, label, low, high, step, decimals in (
            ("min_fill", "Мин. заполнение", 0.0, 1.0, 0.01, 2),
            ("max_aspect", "Макс. aspect", 0.0, 20.0, 0.1, 2),
            ("max_motion_ratio", "Макс. motion", 0.0, 1.0, 0.01, 2),
        ):
            self._make_float(
                form, self._profile_widgets, key, label, profile.get(key),
                low, high, step, decimals,
            )

        note = QLabel(
            "Профили precision/noise используются радаром F и полным поиском H."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        self._profile_combo.currentTextChanged.connect(self._switch_profile)

    def _build_lock_tab(self) -> None:
        layout = self._new_tab("LOCK")
        lock = self._config["LOCK_SETTINGS"]

        sizes = self._new_group(layout, "Размеры")
        for key, label, low, high, step in (
            ("template_size", "Обычный шаблон", 16, 160, 2),
            ("far_size", "FAR", 16, 160, 2),
            ("near_size", "NEAR", 16, 160, 2),
        ):
            self._make_int(
                sizes, self._lock_widgets, key, label, lock.get(key),
                low, high, step,
            )

        search = self._new_group(layout, "Поиск цели")
        for key, label, low, high, step in (
            ("search_margin", "Маржа поиска", 8, 300, 4),
            ("wide_margin", "Широкая маржа", 8, 500, 4),
            ("max_misses", "Макс. промахов", 0, 120, 1),
            ("lost_margin", "Маржа потери", 8, 900, 4),
        ):
            self._make_int(
                search, self._lock_widgets, key, label, lock.get(key),
                low, high, step,
            )
        for key, label, low, high, step, decimals in (
            ("match_min", "Мин. совпадение", 0.05, 0.95, 0.01, 2),
            ("wide_match_min", "Широкое совпадение", 0.05, 0.99, 0.01, 2),
            ("lost_timeout", "Таймаут потери", 0.0, 15.0, 0.1, 2),
            ("lost_match_min", "Совпадение после потери", 0.05, 0.99, 0.01, 2),
            ("update_score", "Порог обновления", 0.5, 0.99, 0.01, 2),
        ):
            self._make_float(
                search, self._lock_widgets, key, label, lock.get(key),
                low, high, step, decimals,
            )

        bank = self._new_group(layout, "Банк шаблонов")
        for key, label, low, high, step in (
            ("template_bank_size", "Размер банка", 0, 8, 1),
        ):
            self._make_int(
                bank, self._lock_widgets, key, label, lock.get(key),
                low, high, step,
            )
        for key, label, low, high, step, decimals in (
            ("bank_add_score", "Порог добавления", 0.0, 0.99, 0.01, 2),
            ("bank_diff_min", "Мин. разница", 0.0, 100.0, 0.1, 2),
        ):
            self._make_float(
                bank, self._lock_widgets, key, label, lock.get(key),
                low, high, step, decimals,
            )

        note = QLabel(
            "FAR должен быть не больше обычного шаблона, NEAR — не меньше. "
            "Широкая маржа и пороги потери должны быть не меньше базовых."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def _build_thermal_tab(self) -> None:
        layout = self._new_tab("Тепловизор")
        thermal = self._config["THERMAL_SETTINGS"]

        form = self._new_group(layout, "Параметры")
        for key, label, low, high, step in (
            ("threshold", "Порог", 1, 80, 1),
            ("dot_size", "Размер точки", 1, 8, 1),
            ("sample_step", "Шаг выборки", 1, 12, 1),
            ("bg_alpha", "Прозрачность фона", 0, 255, 5),
            ("max_dots", "Макс. точек", 1000, 50000, 1000),
        ):
            self._make_int(
                form, self._thermal_widgets, key, label, thermal.get(key),
                low, high, step,
            )

        layout.addWidget(QLabel("Окна Roblox (по одному названию в строке):"))
        self._thermal_windows_edit = QPlainTextEdit()
        self._thermal_windows_edit.setMinimumHeight(100)
        self._thermal_windows_edit.setPlainText(
            "\n".join(str(item) for item in self._config.get("THERMAL_WINDOWS", []))
        )
        layout.addWidget(self._thermal_windows_edit)
        note = QLabel(
            "Пустой список заменяется стандартными названиями окон "
            "Roblox, Roblox Player, ROBLOX."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def _make_int(
        self,
        form: QFormLayout,
        store: Dict[str, QWidget],
        key: str,
        label: str,
        value: Any,
        low: int,
        high: int,
        step: int = 1,
        suffix: str = "",
    ) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(step)
        if suffix:
            widget.setSuffix(suffix)
        widget.setValue(max(low, min(high, _as_int(value, low))))
        store[key] = widget
        form.addRow(label, widget)
        return widget

    def _make_float(
        self,
        form: QFormLayout,
        store: Dict[str, QWidget],
        key: str,
        label: str,
        value: Any,
        low: float,
        high: float,
        step: float,
        decimals: int,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setSingleStep(step)
        if suffix:
            widget.setSuffix(suffix)
        widget.setValue(max(low, min(high, _as_float(value, low))))
        store[key] = widget
        form.addRow(label, widget)
        return widget

    def _make_combo(
        self,
        form: QFormLayout,
        store: Dict[str, QWidget],
        key: str,
        label: str,
        choices: Tuple[Tuple[str, str], ...],
        value: str,
    ) -> QComboBox:
        widget = QComboBox()
        for data, title in choices:
            widget.addItem(title, data)
        index = widget.findData(value)
        widget.setCurrentIndex(index if index >= 0 else 0)
        store[key] = widget
        form.addRow(label, widget)
        return widget

    @staticmethod
    def _widget_value(widget: QWidget) -> Any:
        if isinstance(widget, QSpinBox):
            return int(widget.value())
        if isinstance(widget, QDoubleSpinBox):
            return float(widget.value())
        if isinstance(widget, QCheckBox):
            return bool(widget.isChecked())
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return data if data is not None else widget.currentText()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return None

    @staticmethod
    def _set_widget_value(widget: QWidget, value: Any) -> None:
        if isinstance(widget, QSpinBox):
            widget.setValue(_as_int(value, widget.minimum()))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(_as_float(value, widget.minimum()))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(_as_bool(value, False))

    def _connect_updates(self) -> None:
        widgets: List[QWidget] = []
        widgets.extend(self._top_widgets.values())
        widgets.extend(self._lock_widgets.values())
        widgets.extend(self._thermal_widgets.values())
        widgets.extend(self._profile_widgets.values())
        widgets.extend((self._profile_title, self._profile_adaptive))
        for widget in widgets:
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self._update_status)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self._update_status)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._update_status)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._update_status)
        self._thermal_windows_edit.textChanged.connect(self._update_status)

    def _capture_bind(self, key: str) -> None:
        spec = BIND_BY_KEY[key]
        dialog = KeyCaptureDialog(spec.title, self)
        if dialog.exec_() != QDialog.Accepted or not dialog.key_name:
            return
        self._values[key] = dialog.key_name
        self._buttons[key].setText(display_key(dialog.key_name))
        self._update_status()

    def _capture_center_key(self, key: str) -> None:
        label = next(
            label for name, label, _default in CENTER_KEY_SPECS if name == key
        )
        dialog = KeyCaptureDialog(f"Корректировка: {label}", self)
        if dialog.exec_() != QDialog.Accepted or not dialog.key_name:
            return
        self._center_values[key] = dialog.key_name
        self._center_buttons[key].setText(display_key(dialog.key_name))
        self._update_status()

    def _switch_profile(self, name: str) -> None:
        if not name or name == self._current_profile:
            return
        self._commit_profile()
        self._current_profile = name
        self._load_profile(name)
        self._update_status()

    def _commit_profile(self) -> None:
        if self._loading_profile:
            return
        name = self._current_profile
        if not name:
            return
        profile = self._profiles.setdefault(
            name, copy.deepcopy(DEFAULT_PROFILES["precision"])
        )
        title = self._profile_title.text().strip()
        profile["title"] = title or name.upper()
        profile["adaptive"] = bool(self._profile_adaptive.isChecked())
        for key, widget in self._profile_widgets.items():
            profile[key] = self._widget_value(widget)
        self._update_profile_choice(name, str(profile["title"]))

    def _update_profile_choice(self, name: str, title: str) -> None:
        combo = self._top_widgets.get("ACTIVE_PROFILE")
        if not isinstance(combo, QComboBox):
            return
        index = combo.findData(name)
        if index >= 0:
            combo.setItemText(index, f"{name} — {title}" if title else name)

    def _load_profile(self, name: str) -> None:
        profile = self._profiles.setdefault(
            name, copy.deepcopy(DEFAULT_PROFILES["precision"])
        )
        self._loading_profile = True
        try:
            self._profile_title.setPlaceholderText(name.upper())
            self._profile_title.setText(str(profile.get("title", "")))
            self._profile_adaptive.setChecked(
                _as_bool(profile.get("adaptive"), False)
            )
            for key, widget in self._profile_widgets.items():
                self._set_widget_value(widget, profile.get(key))
        finally:
            self._loading_profile = False

    def _thermal_windows_list(self) -> List[str]:
        text = self._thermal_windows_edit.toPlainText()
        windows = [line.strip() for line in text.splitlines() if line.strip()]
        return windows or list(DEFAULT_THERMAL_WINDOWS)

    def _collect_config(self) -> Dict[str, Any]:
        self._commit_profile()
        config = copy.deepcopy(self._config)
        config["CONFIG_VERSION"] = DEFAULT_CONFIG["CONFIG_VERSION"]

        for key, widget in self._top_widgets.items():
            config[key] = self._widget_value(widget)
        for key, value in self._center_values.items():
            config[key] = value

        binds = config.get("BINDS")
        binds = dict(binds) if isinstance(binds, dict) else {}
        for spec in BIND_SPECS:
            binds[spec.key] = self._values[spec.key]
        config["BINDS"] = binds

        config["SEARCH_PROFILES"] = copy.deepcopy(self._profiles)

        lock = config.get("LOCK_SETTINGS")
        lock = dict(lock) if isinstance(lock, dict) else {}
        for key, widget in self._lock_widgets.items():
            lock[key] = self._widget_value(widget)
        config["LOCK_SETTINGS"] = lock

        thermal = config.get("THERMAL_SETTINGS")
        thermal = dict(thermal) if isinstance(thermal, dict) else {}
        for key, widget in self._thermal_widgets.items():
            thermal[key] = self._widget_value(widget)
        config["THERMAL_SETTINGS"] = thermal
        config["THERMAL_WINDOWS"] = self._thermal_windows_list()

        profiles = config["SEARCH_PROFILES"]
        if config.get("ACTIVE_PROFILE") not in profiles:
            config["ACTIVE_PROFILE"] = next(iter(profiles))
        return config

    def _is_dirty(self) -> bool:
        return self._collect_config() != self._initial_config

    def _update_status(self) -> None:
        if self._loading_profile:
            return
        dirty = self._is_dirty()
        if dirty:
            text = "Есть несохранённые изменения"
            self.setWindowTitle("RADAR Macro — настройка *")
        else:
            text = "Изменений нет"
            self.setWindowTitle("RADAR Macro — настройка")
        self.status_label.setText(text)

    def _show_read_error(self) -> None:
        if self._read_error is None:
            return
        backup = self._backup_bad_config()
        details = f"Не удалось прочитать конфиг:\n{self._read_error}"
        if backup is not None:
            details += f"\n\nСтарый файл сохранён как:\n{backup}"
        details += "\n\nПри сохранении будут записаны настройки из окна."
        QMessageBox.warning(self, "RADAR Macro", details)

    def _backup_bad_config(self) -> Optional[Path]:
        if not CONFIG_FILE.exists():
            return None
        backup = CONFIG_FILE.with_name(CONFIG_FILE.name + ".bad")
        try:
            shutil.copy2(CONFIG_FILE, backup)
            return backup
        except OSError:
            return None

    def _duplicate_groups(self) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for spec in BIND_SPECS:
            groups.setdefault(bind_identity(self._values[spec.key]), []).append(
                spec.title
            )
        for key, label, _default in CENTER_KEY_SPECS:
            groups.setdefault(
                bind_identity(self._center_values[key]), []
            ).append(f"Корректировка: {label}")
        return {name: titles for name, titles in groups.items() if len(titles) > 1}

    def _validate_settings(self) -> bool:
        duplicates = self._duplicate_groups()
        if duplicates:
            lines = []
            for titles in duplicates.values():
                lines.append(", ".join(titles))
            QMessageBox.warning(
                self,
                "Повторяющиеся клавиши",
                "Одна клавиша назначена на несколько действий:\n\n"
                + "\n".join(lines)
                + "\n\nНазначьте разные клавиши.",
            )
            return False

        template = int(self._lock_widgets["template_size"].value())
        far = int(self._lock_widgets["far_size"].value())
        near = int(self._lock_widgets["near_size"].value())
        search = int(self._lock_widgets["search_margin"].value())
        wide = int(self._lock_widgets["wide_margin"].value())
        lost = int(self._lock_widgets["lost_margin"].value())
        match = float(self._lock_widgets["match_min"].value())
        wide_match = float(self._lock_widgets["wide_match_min"].value())
        lost_match = float(self._lock_widgets["lost_match_min"].value())
        problems: List[str] = []
        if far > template:
            problems.append("LOCK: FAR не может быть больше обычного шаблона.")
        if near < template:
            problems.append("LOCK: NEAR не может быть меньше обычного шаблона.")
        if wide < search:
            problems.append("LOCK: широкая маржа не может быть меньше маржи поиска.")
        if lost < wide:
            problems.append("LOCK: маржа потери не может быть меньше широкой маржи.")
        if wide_match < match:
            problems.append("LOCK: широкое совпадение не может быть меньше базового.")
        if lost_match < wide_match:
            problems.append(
                "LOCK: совпадение после потери не может быть меньше широкого."
            )
        for name in self._profiles:
            profile = self._profiles[name]
            if _as_int(profile.get("max_area"), 0) <= _as_int(
                profile.get("min_area"), 0
            ):
                problems.append(
                    f"Профиль {name}: max_area должен быть больше min_area."
                )
        if problems:
            QMessageBox.warning(
                self,
                "Проверьте настройки",
                "\n".join(problems),
            )
            return False
        return True

    def _save_all(self) -> bool:
        self._commit_profile()
        if not self._validate_settings():
            return False

        config = self._collect_config()
        if self._read_error is not None:
            self._backup_bad_config()
        try:
            write_config(CONFIG_FILE, config)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "RADAR Macro",
                f"Не удалось сохранить конфиг:\n{exc}",
            )
            return False

        self._config = copy.deepcopy(config)
        self._initial_config = copy.deepcopy(config)
        self._profiles = copy.deepcopy(config["SEARCH_PROFILES"])
        self._read_error = None
        self._update_status()
        self.status_label.setText(
            "Сохранено. Перезапустите RADAR Macro, чтобы применить настройки."
        )
        return True

    def _reset_binds(self) -> None:
        answer = QMessageBox.question(
            self,
            "Сброс биндов",
            "Сбросить бинды и клавиши корректировки на значения по умолчанию?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._values = dict(DEFAULT_BINDS)
        self._center_values = dict(DEFAULT_CENTER_KEYS)
        for spec in BIND_SPECS:
            self._buttons[spec.key].setText(display_key(self._values[spec.key]))
        for key, _label, _default in CENTER_KEY_SPECS:
            self._center_buttons[key].setText(display_key(self._center_values[key]))
        self._update_status()

    def closeEvent(self, event: QEvent) -> None:
        if self._is_dirty():
            answer = QMessageBox.question(
                self,
                "RADAR Macro",
                "Сохранить изменения перед закрытием?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if answer == QMessageBox.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.Save and not self._save_all():
                event.ignore()
                return
        event.accept()


def main() -> None:
    """Запускает отдельное окно настройки."""
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("RADAR Bind Settings")
    window = BindSettingsWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

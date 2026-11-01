#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Графическая настройка биндов RADAR Macro."""

from __future__ import annotations

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
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
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
    BindSpec("key_center_hold", "Удержание в центре", "N", "A / D / W / S"),
    BindSpec("key_exit", "Выход", "ESCAPE", "завершение"),
    BindSpec("key_hide", "Скрыть / показать", "Ё", "свернуть"),
)

DEFAULT_BINDS: Dict[str, str] = {spec.key: spec.default for spec in BIND_SPECS}
BIND_BY_KEY: Dict[str, BindSpec] = {spec.key: spec for spec in BIND_SPECS}

_DISPLAY_NAMES: Dict[str, str] = {
    "ESCAPE": "ESC",
    "RETURN": "ENTER",
    "BACK": "BACKSPACE",
    "PGUP": "PAGE UP",
    "PGDN": "PAGE DOWN",
    "XBUTTON1": "XBUTTON1",
    "XBUTTON2": "XBUTTON2",
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


def write_binds(
    path: Path,
    values: Dict[str, str],
    base_config: Dict[str, Any],
) -> None:
    """Атомарно записывает BINDS, сохраняя остальные настройки."""
    data = dict(base_config)
    existing = data.get("BINDS")
    binds = dict(existing) if isinstance(existing, dict) else {}
    for spec in BIND_SPECS:
        binds[spec.key] = values[spec.key]
    data["BINDS"] = binds

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
    """Окно со списком действий и текущими клавишами."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RADAR Macro — настройка биндов")
        self.setMinimumSize(700, 600)
        self.resize(760, 700)

        config_data, read_error = read_config()
        self._values = load_bind_values(config_data)
        self._initial = dict(self._values)
        self._buttons: Dict[str, QPushButton] = {}
        self._read_error = read_error

        self._build_ui()
        self._update_status()
        if read_error is not None:
            QTimer.singleShot(0, self._show_read_error)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel("Настройка биндов")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        description = QLabel(
            "Нажмите на кнопку с клавишей, затем нажмите нужную клавишу "
            "или кнопку мыши. Поддерживаются LMB, RMB, MMB, XBUTTON1 и XBUTTON2."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.table = QTableWidget(len(BIND_SPECS), 3, central)
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
        reset_button = QPushButton("Сбросить")
        reset_button.clicked.connect(self._reset_binds)
        save_button = QPushButton("Сохранить")
        save_button.setDefault(True)
        save_button.clicked.connect(self._save_binds)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.close)
        button_row.addWidget(reset_button)
        button_row.addStretch()
        button_row.addWidget(save_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _show_read_error(self) -> None:
        if self._read_error is None:
            return
        backup = self._backup_bad_config()
        details = f"Не удалось прочитать конфиг:\n{self._read_error}"
        if backup is not None:
            details += f"\n\nСтарый файл сохранён как:\n{backup}"
        details += "\n\nПри сохранении будут записаны только бинды."
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

    def _capture_bind(self, key: str) -> None:
        spec = BIND_BY_KEY[key]
        dialog = KeyCaptureDialog(spec.title, self)
        if dialog.exec_() != QDialog.Accepted or not dialog.key_name:
            return
        self._values[key] = dialog.key_name
        self._buttons[key].setText(display_key(dialog.key_name))
        self._update_status()

    def _update_status(self) -> None:
        dirty = self._values != self._initial
        if dirty:
            text = "Есть несохранённые изменения"
            self.setWindowTitle("RADAR Macro — настройка биндов *")
        else:
            text = "Изменений нет"
            self.setWindowTitle("RADAR Macro — настройка биндов")
        self.status_label.setText(text)

    def _duplicates(self) -> Dict[str, List[BindSpec]]:
        groups: Dict[str, List[BindSpec]] = {}
        for spec in BIND_SPECS:
            identity = bind_identity(self._values[spec.key])
            groups.setdefault(identity, []).append(spec)
        return {name: specs for name, specs in groups.items() if len(specs) > 1}

    def _save_binds(self) -> bool:
        duplicates = self._duplicates()
        if duplicates:
            lines = []
            for specs in duplicates.values():
                actions = ", ".join(spec.title for spec in specs)
                lines.append(f"{actions}: {display_key(self._values[specs[0].key])}")
            QMessageBox.warning(
                self,
                "Повторяющиеся бинды",
                "Одна клавиша назначена на несколько действий:\n\n"
                + "\n".join(lines)
                + "\n\nНазначьте разные клавиши.",
            )
            return False

        config_data, read_error = read_config()
        if read_error is not None:
            backup = self._backup_bad_config()
            details = f"Не удалось прочитать конфиг:\n{read_error}"
            if backup is not None:
                details += f"\n\nСтарый файл сохранён как:\n{backup}"
            details += "\n\nБудут сохранены только бинды."
            QMessageBox.warning(self, "RADAR Macro", details)
            config_data = {}

        try:
            write_binds(CONFIG_FILE, self._values, config_data)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "RADAR Macro",
                f"Не удалось сохранить конфиг:\n{exc}",
            )
            return False

        self._initial = dict(self._values)
        self._read_error = None
        self._update_status()
        self.status_label.setText(
            "Сохранено. Перезапустите RADAR Macro, чтобы применить бинды."
        )
        return True

    def _reset_binds(self) -> None:
        answer = QMessageBox.question(
            self,
            "Сброс биндов",
            "Сбросить все бинды на значения по умолчанию?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._values = dict(DEFAULT_BINDS)
        for spec in BIND_SPECS:
            self._buttons[spec.key].setText(display_key(self._values[spec.key]))
        self._update_status()

    def closeEvent(self, event: QEvent) -> None:
        if self._values != self._initial:
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
            if answer == QMessageBox.Save and not self._save_binds():
                event.ignore()
                return
        event.accept()


def main() -> None:
    """Запускает отдельное окно настройки биндов."""
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("RADAR Bind Settings")
    window = BindSettingsWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

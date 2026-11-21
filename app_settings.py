# app_settings.py
import json
import os

from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QDialogButtonBox, QVBoxLayout,
)

DEFAULTS = {
    "player": "",
    "refresh_sec": 10,
    "thumb_workers": 3,
    "thumb_gap": 0.30,
    "visible_only": True,
}


def load_settings(path):
    data = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data.update(json.load(f) or {})
    except Exception:
        pass
    return data


def save_settings(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DDnetGI 设置")
        self.setMinimumWidth(420)

        self.player_edit = QLineEdit(settings.get("player", ""))
        self.player_edit.setPlaceholderText("必填，例如：adyy6")

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(3, 300)
        self.refresh_spin.setSuffix(" 秒")
        self.refresh_spin.setValue(int(settings.get("refresh_sec", 10)))

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        self.workers_spin.setValue(int(settings.get("thumb_workers", 3)))

        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.05, 2.0)
        self.gap_spin.setSingleStep(0.05)
        self.gap_spin.setDecimals(2)
        self.gap_spin.setSuffix(" 秒/请求")
        self.gap_spin.setValue(float(settings.get("thumb_gap", 0.30)))

        self.visible_check = QCheckBox("只在可见区域加载缩略图")
        self.visible_check.setChecked(bool(settings.get("visible_only", True)))

        form = QFormLayout()
        form.addRow("主玩家名称:", self.player_edit)
        form.addRow("服务器刷新间隔:", self.refresh_spin)
        form.addRow("缩略图并发数:", self.workers_spin)
        form.addRow("缩略图请求间隔:", self.gap_spin)
        form.addRow("", self.visible_check)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("保存")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(box)

    def _on_accept(self):
        if not self.player_edit.text().strip():
            self.player_edit.setFocus()
            return
        self.accept()

    def values(self):
        return {
            "player": self.player_edit.text().strip(),
            "refresh_sec": self.refresh_spin.value(),
            "thumb_workers": self.workers_spin.value(),
            "thumb_gap": round(self.gap_spin.value(), 2),
            "visible_only": self.visible_check.isChecked(),
        }

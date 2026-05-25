# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QRadioButton, QDialogButtonBox, QGroupBox, QWidget
)

class BlocklopDialog(QDialog):
    """
    เลือก 2 โหมด:
      1) ตัดแบ่งส่วน (แนวตั้ง/แนวนอน อิงมุม Rotate ของแผนที่)
      2) ลากเส้นด้วยเมาส์เพื่อกำหนดทิศทาง แล้วตัดแบ่งส่วน (เส้นตัดตาม Line ที่ลาก)
    """
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle("Blocklop – ตั้งค่าการแบ่งโพลิกอน")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)

        # -------- Mode choice --------
        gbMode = QGroupBox("เลือกโหมด")
        vMode = QVBoxLayout(gbMode)
        self._rbModeVH = QRadioButton("หมุนตามมุม Rotate แล้วตัดแบ่งส่วน (แนวตั้ง/แนวนอน)")
        self._rbModeDrag = QRadioButton("ใช้เม้าส์ลากเส้นกำหนดทิศทาง แล้วแบ่งส่วน")
        self._rbModeVH.setChecked(True)
        vMode.addWidget(self._rbModeVH)
        vMode.addWidget(self._rbModeDrag)
        root.addWidget(gbMode)

        # -------- N parts (always enabled) --------
        gbN = QGroupBox("จำนวนส่วนที่จะแบ่ง")
        lnN = QHBoxLayout(gbN)
        lnN.addWidget(QLabel("N:"))
        self._spinN = QSpinBox()
        self._spinN.setRange(2, 1000)
        self._spinN.setValue(3)
        lnN.addWidget(self._spinN, 1)
        root.addWidget(gbN)

        # -------- Direction for VH mode only --------
        gbDir = QGroupBox("ทิศทางสำหรับโหมดแนวตั้ง/แนวนอน")
        lnVH = QHBoxLayout(gbDir)
        self._rbVertical = QRadioButton("Vertical (ตั้ง)")
        self._rbHorizontal = QRadioButton("Horizontal (นอน)")
        self._rbVertical.setChecked(True)
        lnVH.addWidget(self._rbVertical)
        lnVH.addWidget(self._rbHorizontal)
        root.addWidget(gbDir)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        # Wiring: toggle direction group by mode
        self._gbDir = gbDir
        self._rbModeVH.toggled.connect(self._update_visibility)
        self._rbModeDrag.toggled.connect(self._update_visibility)
        self._update_visibility()

    # ----------------- getters used by Blocklop -----------------
    def is_mode_drag(self) -> bool:
        return bool(self._rbModeDrag.isChecked())

    def parts(self) -> int:
        return int(self._spinN.value())

    def is_vertical(self) -> bool:
        return bool(self._rbVertical.isChecked())

    # ----------------- UI helpers -----------------
    def _update_visibility(self):
        # แสดง/ซ่อนชุดตัวเลือกแนวตั้ง/แนวนอนเฉพาะตอนโหมด VH
        self._gbDir.setEnabled(self._rbModeVH.isChecked())

# -*- coding: utf-8 -*-
"""
Blocklop – Divide Polygon into N parts
Modes:
  1) Vertical / Horizontal (canvas-rotation aware)
  2) Drag a line with mouse (cuts PARALLEL to dragged line)

Flow: Click plugin → pick polygon → open dialog → (optionally drag line) → split

Notes:
- If field OBJECTID exists, it will be auto-filled with unique integers starting at >= 900000000.
- If OBJECTID field does not exist, it is skipped (no error).
- After splitting, all newly created parts will be selected.
- Cancel on dialog shows Yes/No; Yes closes plugin and rolls back edits (if editing).
- Toolbar button uses an icon from resources (SVG). Make sure resources.qrc includes icons/blocklop.svg
  and is compiled to resources.py (pyrcc5 -o resources.py resources.qrc).
"""

import os.path
import math

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, QTimer, Qt
from qgis.PyQt.QtGui import QIcon, QCursor
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from .resources import *  # requires resources.qrc compiled to resources.py
from .Blocklop_dialog import BlocklopDialog

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsWkbTypes, QgsCoordinateReferenceSystem, QgsMapLayer, QgsCoordinateTransform
)
from qgis.utils import iface
from qgis.gui import QgsMapToolIdentifyFeature, QgsMapTool, QgsRubberBand

import processing


# ----------------- Map Tool: pick polygon -----------------
class BlocklopPickTool(QgsMapToolIdentifyFeature):
    def __init__(self, iface, layer, after_pick_callback):
        super().__init__(iface.mapCanvas())
        self.iface = iface
        self.layer = layer
        self.after_pick_callback = after_pick_callback

    def canvasReleaseEvent(self, event):
        res = self.identify(event.x(), event.y(), [self.layer], self.TopDownStopAtFirst)
        if not res:
            self.iface.messageBar().pushWarning("Blocklop", "คลิกไม่โดนฟีเจอร์ในเลเยอร์นี้")
            return
        fid = res[0].mFeature.id()
        self.layer.removeSelection()
        self.layer.select(fid)
        try:
            self.after_pick_callback()
        finally:
            try:
                self.iface.actionPan().trigger()
            except Exception:
                pass


# ----------------- Map Tool: drag a line (2 points) -----------------
class BlocklopDragLineTool(QgsMapTool):
    """
    Drag a straight line:
      - Left press → move → left release = one segment
      - Or: click once to anchor P0, move, click again to set P1
      - Esc to cancel
    Resulting cut lines will be PARALLEL to the dragged direction.
    """
    def __init__(self, iface, target_layer, finished_callback, cancelled_callback=None):
        super().__init__(iface.mapCanvas())
        self.iface = iface
        self.layer = target_layer
        self.finished_callback = finished_callback
        self.cancelled_callback = cancelled_callback
        self.p0 = None
        self.p1 = None
        self.dragging = False
        self.have_anchor = False

        self.rb = QgsRubberBand(self.iface.mapCanvas(), Qgis.GeometryType.Line)
        try:
            self.rb.setWidth(2)
        except Exception:
            pass

    def activate(self):
        super().activate()
        try:
            self.setCursor(QCursor(Qt.CrossCursor))
            self.iface.mapCanvas().setCursor(QCursor(Qt.CrossCursor))
        except Exception:
            pass
        try:
            self.iface.messageBar().pushInfo(
                "Blocklop – ลากเส้นกำหนดทิศทาง",
                "กดซ้ายค้างแล้วลากพาดผ่าน polygon จากนั้นปล่อยเพื่อจบ; "
                "หรือคลิก-คลิกสองครั้งกำหนดปลายทั้งสอง; กด Esc เพื่อยกเลิก"
            )
        except Exception:
            pass

    def deactivate(self):
        self._clear_rb()
        super().deactivate()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._clear_rb()
            if self.cancelled_callback:
                self.cancelled_callback()
            try:
                self.iface.actionPan().trigger()
            except Exception:
                pass

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            try:
                mp = event.mapPoint()
            except Exception:
                mp = None
            if mp is None:
                return
            if not self.have_anchor:
                self.p0 = QgsPointXY(mp)
            self._update_preview(mp)

    def canvasMoveEvent(self, event):
        if not self.dragging and not self.have_anchor:
            return
        try:
            mp = event.mapPoint()
        except Exception:
            return
        self._update_preview(mp)

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        try:
            mp = event.mapPoint()
        except Exception:
            mp = None

        if not self.have_anchor:
            # first anchor fixed
            self.have_anchor = True
            self.dragging = False
            if mp is not None:
                self._update_preview(mp)
            return

        # second anchor -> finish
        self.dragging = False
        if mp is None:
            self._clear_rb()
            if self.cancelled_callback:
                self.cancelled_callback()
            return

        self.p1 = QgsPointXY(mp)
        if self.p0 is None or (self.p0.x() == self.p1.x() and self.p0.y() == self.p1.y()):
            try:
                self.iface.messageBar().pushWarning("Blocklop", "เส้นสั้นเกินไป/ไม่มีทิศทาง")
            except Exception:
                pass
            self._clear_rb()
            if self.cancelled_callback:
                self.cancelled_callback()
            return

        # Build geometry in layer CRS
        g = QgsGeometry.fromPolylineXY([self.p0, self.p1])
        try:
            canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
            if self.layer and self.layer.crs() and self.layer.crs() != canvas_crs:
                xform = QgsCoordinateTransform(canvas_crs, self.layer.crs(), QgsProject.instance())
                g.transform(xform)
        except Exception:
            pass

        self._clear_rb()
        if self.finished_callback:
            self.finished_callback(g)
        try:
            self.iface.actionPan().trigger()
        except Exception:
            pass

    def _update_preview(self, mp):
        if self.p0 is None:
            return
        p1 = QgsPointXY(mp) if isinstance(mp, QgsPointXY) else QgsPointXY(mp.x(), mp.y())
        try:
            self.rb.setToGeometry(QgsGeometry.fromPolylineXY([self.p0, p1]), None)
        except Exception:
            pass

    def _clear_rb(self):
        try:
            self.rb.reset(Qgis.GeometryType.Line)
        except Exception:
            pass
        self.p0 = None
        self.p1 = None
        self.dragging = False
        self.have_anchor = False


# ----------------- Plugin class -----------------
class Blocklop:
    def __init__(self, iface_):
        self.iface = iface_
        self.plugin_dir = os.path.dirname(__file__)
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(self.plugin_dir, 'i18n', f'Blocklop_{locale}.qm')
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr(u'&Blocklop')
        self.first_start = None

        # runtime states
        self.dlg = None
        self._pick_tool = None
        self._drag_tool = None
        self._pending_layer = None

        # enable/disable
        self._sel_conn_layer = None

        # optional dedicated toolbar
        self._toolbar = None

    def tr(self, message):
        return QCoreApplication.translate('Blocklop', message)

    # ---------- Icon loader (qrc + filesystem fallback) ----------
    def _load_icon(self):
        icon = QIcon(':/plugins/Blocklop/blocklop.svg')  # from qrc
        if icon.isNull():
            fallback = os.path.join(self.plugin_dir, 'icons', 'blocklop.svg')
            if os.path.exists(fallback):
                icon = QIcon(fallback)
        return icon

    def add_action(self, icon_path, text, callback,
                   enabled_flag=True, add_to_menu=True, add_to_toolbar=False,
                   status_tip=None, whats_this=None, parent=None):
        """Slightly modified: default add_to_toolbar=False to avoid duplicate buttons."""
        icon = QIcon(icon_path) if icon_path else QIcon()
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.iface.addToolBarIcon(action)  # (not used by default here)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        # Create (or reuse) a dedicated toolbar for Blocklop
        try:
            if self._toolbar is None:
                self._toolbar = self.iface.addToolBar('Blocklop')
                self._toolbar.setObjectName('BlocklopToolbar')
        except Exception:
            self._toolbar = None

        # Create action without path icon; set icon explicitly
        self.action = self.add_action(
            icon_path=None,
            text=self.tr(u'Divide Polygon (Blocklop)'),
            callback=self.run,
            parent=self.iface.mainWindow(),
            status_tip=self.tr(u'แบ่งโพลิกอนเป็น N ส่วน หรือ ลากเส้นกำหนดทิศทาง'),
            whats_this=self.tr(u'Blocklop – Click to pick polygon, then configure'),
            add_to_menu=True,      # show in menu
            add_to_toolbar=False   # we'll add to our own toolbar below
        )

        # Set icon (qrc or fallback) and show in menu
        try:
            self.action.setIcon(self._load_icon())
            self.action.setIconVisibleInMenu(True)
            self.action.setToolTip("Blocklop – Divide Polygon")
        except Exception:
            pass

        # Put the action on our dedicated toolbar (icon-only button)
        try:
            if self._toolbar is not None:
                self._toolbar.addAction(self.action)
                self._toolbar.setVisible(True)
        except Exception:
            pass

        # Also add to the main Plugins toolbar for visibility
        try:
            self.iface.addToolBarIcon(self.action)
        except Exception:
            pass

        self.first_start = True

        try:
            self.action.setEnabled(False)
        except Exception:
            pass
        try:
            self.iface.currentLayerChanged.connect(self._on_current_layer_changed)
        except Exception:
            pass
        QTimer.singleShot(0, lambda: self._on_current_layer_changed(self.iface.activeLayer()))

    def unload(self):
        try:
            self.iface.currentLayerChanged.disconnect(self._on_current_layer_changed)
        except Exception:
            pass
        self._disconnect_prev_selection()
        self._reset_for_new_session(silent=True)

        # remove from Plugin Menu and Toolbar
        for action in self.actions:
            try:
                self.iface.removePluginMenu(self.tr(u'&Blocklop'), action)
            except Exception:
                pass
            try:
                self.iface.removeToolBarIcon(action)
            except Exception:
                pass

        # remove dedicated toolbar
        try:
            if self._toolbar is not None:
                self._toolbar.removeAction(self.action)
                self.iface.mainWindow().removeToolBar(self._toolbar)
                self._toolbar = None
        except Exception:
            pass

    # ----------------- Main entry -----------------
    def run(self):
        self._reset_for_new_session(silent=True)

        layer = iface.activeLayer()
        if not layer:
            self.iface.messageBar().pushWarning("Blocklop", "กรุณาเลือกเลเยอร์โพลิกอนก่อน")
            return
        if layer.type() != QgsMapLayer.VectorLayer or QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PolygonGeometry:
            self.iface.messageBar().pushWarning("Blocklop", "เลเยอร์ที่เลือกไม่ใช่ Polygon")
            return

        if self.first_start is True or self.dlg is None:
            self.first_start = False
            self.dlg = BlocklopDialog()
            try:
                self.dlg.setWindowIcon(self._load_icon())
            except Exception:
                pass
            try:
                self.dlg.rejected.connect(self._on_dialog_rejected)
            except Exception:
                pass

        self._pending_layer = layer
        self.iface.messageBar().pushInfo("Blocklop", "คลิกที่ polygon ที่ต้องการแบ่ง จากนั้นจะเปิดหน้าตั้งค่า")
        self._pick_tool = BlocklopPickTool(self.iface, layer, self._show_dialog_then_process)
        self.iface.mapCanvas().setMapTool(self._pick_tool)

    # ---------- Cancel (Yes/No) ----------
    def _on_dialog_rejected(self):
        try:
            ret = QMessageBox.question(
                self.iface.mainWindow(),
                "Blocklop",
                "ต้องการยกเลิกและปิดปลั๊กอินหรือไม่?\n(จะปิดและเลิกโหมดแก้ไขโดยไม่บันทึก)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
        except Exception:
            ret = QMessageBox.No

        if ret == QMessageBox.Yes:
            layer = self.iface.activeLayer()
            try:
                if layer and layer.type() == QgsMapLayer.VectorLayer and layer.isEditable():
                    layer.rollBack()
            except Exception:
                pass
            self._reset_for_new_session()
            try:
                self.iface.messageBar().pushInfo("Blocklop", "ยกเลิกและปิดปลั๊กอินแล้ว")
            except Exception:
                pass
        else:
            try:
                self.dlg.show(); self.dlg.raise_(); self.dlg.activateWindow()
            except Exception:
                pass

    # ---------- Enable/Disable ----------
    def _disconnect_prev_selection(self):
        try:
            if getattr(self, "_sel_conn_layer", None):
                self._sel_conn_layer.selectionChanged.disconnect(self._on_selection_changed)
        except Exception:
            pass
        self._sel_conn_layer = None

    def _on_current_layer_changed(self, layer):
        self._disconnect_prev_selection()
        try:
            if layer and layer.type() == QgsMapLayer.VectorLayer and QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.PolygonGeometry:
                layer.selectionChanged.connect(self._on_selection_changed)
                self._sel_conn_layer = layer
        except Exception:
            pass
        self._update_action_enabled()

    def _on_selection_changed(self, *args, **kwargs):
        self._update_action_enabled()

    def _update_action_enabled(self):
        try:
            layer = self.iface.activeLayer()
            enabled = bool(layer and layer.type() == QgsMapLayer.VectorLayer and QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.PolygonGeometry)
            self.action.setEnabled(enabled)
        except Exception:
            pass

    # ----------------- Dialog → process -----------------
    def _show_dialog_then_process(self):
        self.dlg.show()
        if not self.dlg.exec_():
            return

        layer = getattr(self, "_pending_layer", None) or self.iface.activeLayer()
        if not layer or layer.type() != QgsMapLayer.VectorLayer or QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PolygonGeometry:
            self.iface.messageBar().pushWarning("Blocklop", "เลเยอร์ไม่ถูกต้องหรือไม่ได้เป็น Polygon")
            return
        if layer.selectedFeatureCount() == 0:
            self.iface.messageBar().pushWarning("Blocklop", "กรุณาเลือกฟีเจอร์ (polygon) อย่างน้อย 1 ชิ้นก่อนใช้งาน")
            return

        # อ่านค่าจาก Dialog (รองรับ fallback)
        get_bool = lambda name, default: bool(getattr(self.dlg, name, lambda: default)())
        get_int = lambda name, default: int(getattr(self.dlg, name, lambda: default)())

        N = get_int("parts", 3)
        is_drag_mode = get_bool("is_mode_drag", False)
        vertical = get_bool("is_vertical", True)

        if is_drag_mode:
            QTimer.singleShot(0, lambda: self._start_drag_direction_mode(layer, N))
            return
        else:
            cut_lines = self._build_cut_lines_layer(
                poly_layer=layer, N=N, vertical=vertical, use_canvas_rotation=True
            )
            if not cut_lines or cut_lines.featureCount() == 0:
                self.iface.messageBar().pushWarning("Blocklop", "ไม่พบเส้นตัดที่สร้างได้")
                return
            self._process_split(layer, cut_lines)

    # ---------- Drag-direction mode ----------
    def _start_drag_direction_mode(self, layer, N):
        try:
            self.iface.messageBar().pushInfo(
                "Blocklop",
                "โหมดลากเส้น: กดซ้ายค้างแล้วลากพาดผ่าน polygon หรือคลิกสองครั้งกำหนดปลายทั้งสอง จากนั้นปล่อย/คลิกเพื่อจบ"
            )
        except Exception:
            pass
        self._drag_tool = BlocklopDragLineTool(
            self.iface, layer,
            finished_callback=lambda geom_line: self._on_drag_line_finished(layer, geom_line, N),
            cancelled_callback=self._on_drag_line_cancelled
        )
        self.iface.mapCanvas().setMapTool(self._drag_tool)

    def _on_drag_line_finished(self, layer, geom_line, N):
        if not layer or not isinstance(geom_line, QgsGeometry):
            self._on_drag_line_cancelled()
            return
        cut_lines = self._build_cut_lines_by_direction(layer, N, geom_line)
        if not cut_lines or cut_lines.featureCount() == 0:
            try:
                self.iface.messageBar().pushWarning("Blocklop", "ไม่สามารถสร้างเส้นตัดจากเส้นที่ลากได้")
            except Exception:
                pass
        else:
            self._process_split(layer=layer, cut_lines=cut_lines)
        self._drag_tool = None

    def _on_drag_line_cancelled(self):
        self._drag_tool = None
        try:
            self.iface.messageBar().pushInfo("Blocklop", "ยกเลิกโหมดลากเส้น")
        except Exception:
            pass

    # ---------- Core splitting ----------
    def _process_split(self, layer, cut_lines):
        input_layer = self._layer_from_selection(layer, "Blocklop_Selected")
        params = {'INPUT': input_layer, 'LINES': cut_lines, 'OUTPUT': 'memory:Blocklop_Split'}
        try:
            res = processing.run("native:splitwithlines", params)
        except Exception as e:
            self.iface.messageBar().pushCritical("Blocklop", "Split failed: {}".format(e))
            return

        out_lyr = res.get('OUTPUT')
        if not isinstance(out_lyr, QgsVectorLayer):
            self.iface.messageBar().pushCritical("Blocklop", "ไม่สามารถสร้างผลลัพธ์ได้")
            return

        try:
            selected_ids = layer.selectedFeatureIds()
            if not selected_ids:
                self.iface.messageBar().pushWarning("Blocklop", "ไม่ได้เลือกฟีเจอร์ไว้ — ไม่สามารถเขียนทับเลเยอร์เดิมได้")
                return

            orig_feats = list(input_layer.getFeatures())
            if not orig_feats:
                self.iface.messageBar().pushCritical("Blocklop", "ไม่พบฟีเจอร์ต้นฉบับเพื่อคัดลอกแอตทริบิวต์")
                return

            if not layer.isEditable():
                layer.startEditing()

            layer.beginEditCommand("Blocklop split in-place")

            if not layer.deleteFeatures(selected_ids):
                layer.destroyEditCommand()
                self.iface.messageBar().pushCritical("Blocklop", "ลบฟีเจอร์เดิมไม่สำเร็จ")
                return

            # OBJECTID (optional): start from max or >= 900000000
            ID_FIELD_NAME = None
            for fld in layer.fields():
                if fld.name().upper() == "OBJECTID":
                    ID_FIELD_NAME = fld.name()
                    break

            next_objid = None
            if ID_FIELD_NAME is not None:
                max_objid = 900000000 - 1
                try:
                    for f in layer.getFeatures():
                        v = f.attribute(ID_FIELD_NAME)
                        if v is None:
                            continue
                        try:
                            vi = int(v)
                            if vi > max_objid:
                                max_objid = vi
                        except Exception:
                            continue
                except Exception:
                    pass
                next_objid = max_objid + 1

            try:
                before_ids = set(f.id() for f in layer.getFeatures())
            except Exception:
                before_ids = set()

            new_feats = []
            for new_part in out_lyr.getFeatures():
                geom = new_part.geometry()
                try:
                    valid_flag = geom.isGeosValid() if hasattr(geom, "isGeosValid") else geom.isValid()
                    if not valid_flag:
                        geom = geom.makeValid()
                except Exception:
                    pass

                donor = None
                for of in orig_feats:
                    try:
                        if geom.intersects(of.geometry()):
                            donor = of
                            break
                    except Exception:
                        continue
                if donor is None:
                    donor = orig_feats[0]

                fnew = QgsFeature(layer.fields())
                fnew.setGeometry(geom)

                donor_names = donor.fields().names()
                attrs = []
                for fld in layer.fields():
                    name = fld.name()
                    if ID_FIELD_NAME is not None and name == ID_FIELD_NAME:
                        attrs.append(next_objid)
                        next_objid += 1
                    else:
                        attrs.append(donor[name] if name in donor_names else None)
                fnew.setAttributes(attrs)
                new_feats.append(fnew)

            ok = layer.addFeatures(new_feats)
            if not ok:
                layer.destroyEditCommand()
                self.iface.messageBar().pushCritical("Blocklop", "เพิ่มฟีเจอร์ใหม่ไม่สำเร็จ — ยกเลิกการแก้ไข")
                return

            layer.endEditCommand()

            # Select เฉพาะชิ้นที่เพิ่มใหม่ทั้งหมด
            try:
                layer.removeSelection()
                new_ids = []
                try:
                    after_ids = set(f.id() for f in layer.getFeatures())
                    new_ids = list(after_ids - before_ids)
                except Exception:
                    new_ids = []
                if not new_ids:
                    # fallback แบบ spatial
                    try:
                        new_geoms = [f.geometry() for f in new_feats]
                        cand = []
                        for f in layer.getFeatures():
                            try:
                                if any(f.geometry().intersects(g) for g in new_geoms):
                                    cand.append(f.id())
                            except Exception:
                                continue
                        new_ids = cand
                    except Exception:
                        pass
                if new_ids:
                    layer.selectByIds(new_ids)
            except Exception:
                pass

            self.iface.messageBar().pushSuccess("Blocklop", "ตัดเสร็จแล้ว")
        except Exception as e:
            try:
                layer.destroyEditCommand()
            except Exception:
                pass
            self.iface.messageBar().pushCritical("Blocklop", "เกิดข้อผิดพลาดระหว่างตัดแบบ in-place: {}".format(e))

        self._reset_after_run()

    # ----------------- Builders -----------------
    def _layer_from_selection(self, src_layer: QgsVectorLayer, name: str) -> QgsVectorLayer:
        crs = src_layer.crs().authid() if isinstance(src_layer.crs(), QgsCoordinateReferenceSystem) else "EPSG:4326"
        wkb_str = QgsWkbTypes.displayString(src_layer.wkbType())
        mem = QgsVectorLayer(f"{wkb_str}?crs={crs}", name, "memory")
        mem_pr = mem.dataProvider()
        mem_pr.addAttributes(src_layer.fields())
        mem.updateFields()
        feats = list(src_layer.getSelectedFeatures())
        if feats:
            mem_pr.addFeatures(feats)
        mem.updateExtents()
        return mem

    def _build_cut_lines_layer(self, poly_layer: QgsVectorLayer, N: int, vertical: bool,
                               use_canvas_rotation: bool=True) -> QgsVectorLayer:
        """Axis-aligned with optional canvas rotation."""
        if N < 2:
            return None
        crs = poly_layer.crs().authid() if isinstance(poly_layer.crs(), QgsCoordinateReferenceSystem) else "EPSG:4326"
        line_layer = QgsVectorLayer(f"LineString?crs={crs}", "Blocklop_CutLines", "memory")
        pr = line_layer.dataProvider()

        canvas_angle_deg = 0.0
        if use_canvas_rotation:
            try:
                canvas_angle_deg = float(self.iface.mapCanvas().rotation())
            except Exception:
                canvas_angle_deg = 0.0

        for f in poly_layer.getSelectedFeatures():
            geom = f.geometry()
            if not geom or geom.isEmpty():
                continue

            if abs(canvas_angle_deg) > 1e-9:
                # เส้นตัดให้ตรงกับมุมหมุนของ canvas:
                # vertical=True  → ใช้มุม rotation เอง (แกนตั้ง) แล้ววาดเส้น "ตั้งฉาก" กับแกนนั้น
                # vertical=False → rotation + 90 องศา แล้ววาดเส้น "ตั้งฉาก" กับแกนนั้น
                angle = canvas_angle_deg + (0.0 if vertical else 90.0)
                self._add_lines_for_feature_at_angle(pr, geom, N, angle, perpendicular=True)
                continue

            bb = geom.boundingBox()
            minX, maxX = bb.xMinimum(), bb.xMaximum()
            minY, maxY = bb.yMinimum(), bb.yMaximum()
            width = maxX - minX
            height = maxY - minY
            if width <= 0 or height <= 0:
                continue

            dx = width * 0.1
            dy = height * 0.1

            for i in range(1, N):
                if vertical:
                    x = minX + (width * i) / N
                    p1 = QgsPointXY(x, minY - dy)
                    p2 = QgsPointXY(x, maxY + dy)
                else:
                    y = minY + (height * i) / N
                    p1 = QgsPointXY(minX - dx, y)
                    p2 = QgsPointXY(maxX + dx, y)
                g_line = QgsGeometry.fromPolylineXY([p1, p2])
                feat = QgsFeature()
                feat.setGeometry(g_line)
                pr.addFeatures([feat])

        line_layer.updateExtents()
        return line_layer

    def _add_lines_for_feature_at_angle(self, provider, geom: QgsGeometry, N: int,
                                        angle_deg: float, perpendicular: bool = True):
        """วาดเส้นตัด N-1 เส้นสำหรับฟีเจอร์หนึ่งชิ้น
        - perpendicular=True  → เส้นตัดตั้งฉากกับทิศ angle_deg
        - perpendicular=False → เส้นตัดขนานกับทิศ angle_deg
        """
        pts = []
        try:
            it = geom.vertices()
            for p in it:
                pts.append((p.x(), p.y()))
        except Exception:
            return
        if len(pts) < 2:
            return

        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)

        theta = math.radians(angle_deg)
        ux, uy = math.cos(theta), math.sin(theta)   # แกนหลัก (ทิศ angle_deg)
        vx, vy = -uy, ux                            # แกนตั้งฉาก

        s_vals, t_vals = [], []
        for x, y in pts:
            dx = x - cx
            dy = y - cy
            s_vals.append(dx*ux + dy*uy)  # ระยะตามแกน u
            t_vals.append(dx*vx + dy*vy)  # ระยะตามแกน v

        min_s, max_s = min(s_vals), max(s_vals)
        min_t, max_t = min(t_vals), max(t_vals)

        ds = (max_s - min_s) * 0.1
        dt = (max_t - min_t) * 0.1

        if perpendicular:
            # ตั้งฉากกับทิศ angle: fix s แล้วลากตามแกน v
            for i in range(1, N):
                si = min_s + (max_s - min_s) * i / N
                t1 = min_t - dt
                t2 = max_t + dt
                x1 = cx + si*ux + t1*vx; y1 = cy + si*uy + t1*vy
                x2 = cx + si*ux + t2*vx; y2 = cy + si*uy + t2*vy
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x1, y1), QgsPointXY(x2, y2)]))
                provider.addFeatures([feat])
        else:
            # ขนานกับทิศ angle: fix t แล้วลากตามแกน u
            for i in range(1, N):
                ti = min_t + (max_t - min_t) * i / N
                s1 = min_s - ds
                s2 = max_s + ds
                x1 = cx + s1*ux + ti*vx; y1 = cy + s1*uy + ti*vy
                x2 = cx + s2*ux + ti*vx; y2 = cy + s2*uy + ti*vy
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x1, y1), QgsPointXY(x2, y2)]))
                provider.addFeatures([feat])

    def _build_cut_lines_by_direction(self, poly_layer: QgsVectorLayer, N: int, drag_line: QgsGeometry) -> QgsVectorLayer:
        """
        ใช้ทิศทางของเส้นที่ลาก (drag_line) แล้วสร้างเส้นตัด N-1 เส้นที่ 'ขนาน' กับทิศนั้น
        ครอบ bbox ของแต่ละ polygon ที่ถูกเลือก
        """
        if N < 2 or not drag_line or drag_line.isEmpty():
            return None

        try:
            seg = drag_line.asPolyline()
            if not seg or len(seg) < 2:
                return None
            p0, p1 = seg[0], seg[-1]
            dx, dy = (p1.x() - p0.x()), (p1.y() - p0.y())
            if abs(dx) < 1e-12 and abs(dy) < 1e-12:
                return None
            angle_deg = math.degrees(math.atan2(dy, dx))
        except Exception:
            return None

        crs = poly_layer.crs().authid() if isinstance(poly_layer.crs(), QgsCoordinateReferenceSystem) else "EPSG:4326"
        line_layer = QgsVectorLayer(f"LineString?crs={crs}", "Blocklop_CutLines_Dir", "memory")
        pr = line_layer.dataProvider()

        for f in poly_layer.getSelectedFeatures():
            geom = f.geometry()
            if not geom or geom.isEmpty():
                continue
            # ใช้เส้นตัด “ขนานกับเส้นที่ลาก”
            self._add_lines_for_feature_at_angle(pr, geom, N, angle_deg, perpendicular=False)

        line_layer.updateExtents()
        return line_layer

    # ----------------- Small resets -----------------
    def _reset_for_new_session(self, silent=False):
        try:
            if self._pick_tool and self.iface.mapCanvas().mapTool() is self._pick_tool:
                self.iface.mapCanvas().unsetMapTool(self._pick_tool)
        except Exception:
            pass
        self._pick_tool = None

        try:
            if self._drag_tool and self.iface.mapCanvas().mapTool() is self._drag_tool:
                self.iface.mapCanvas().unsetMapTool(self._drag_tool)
        except Exception:
            pass
        self._drag_tool = None

        self._pending_layer = None

        try:
            if self.dlg:
                self.dlg.hide()
        except Exception:
            pass

        try:
            layer = self.iface.activeLayer()
            if layer and layer.type() == QgsMapLayer.VectorLayer:
                layer.removeSelection()
        except Exception:
            pass

        if not silent:
            try:
                self.iface.messageBar().pushInfo("Blocklop", "ปิด Blocklop แล้ว พร้อมเริ่มใหม่")
            except Exception:
                pass

    def _reset_after_run(self):
        try:
            if self._pick_tool and self.iface.mapCanvas().mapTool() is self._pick_tool:
                self.iface.mapCanvas().unsetMapTool(self._pick_tool)
        except Exception:
            pass
        self._pick_tool = None
        try:
            if self._drag_tool and self.iface.mapCanvas().mapTool() is self._drag_tool:
                self.iface.mapCanvas().unsetMapTool(self._drag_tool)
        except Exception:
            pass
        self._drag_tool = None
        self._pending_layer = None

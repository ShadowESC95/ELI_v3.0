"""widgets — extracted from gui_eli_original.py (v3 modularisation)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class _FlowLayout(QLayout):
    """Wrapping flow layout — items fill left-to-right then wrap."""

    def __init__(self, parent=None, h_spacing: int = 8, v_spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self._h = h_spacing
        self._v = v_spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        sz = QSize()
        for it in self._items:
            sz = sz.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return sz + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRect, dry: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row_h = eff.x(), eff.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._h
            if nx - self._h > eff.right() and row_h > 0:
                x, y = eff.x(), y + row_h + self._v
                nx = x + hint.width() + self._h
                row_h = 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x, row_h = nx, max(row_h, hint.height())
        return y + row_h - rect.y() + m.bottom()


class _CapabilityList(QListWidget):
    """Draggable list that emits the capability name via MIME text/plain."""

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if not item:
            return
        drag = QDrag(self)
        mime = QMimeData()
        # Store only the raw action name (strip description suffix)
        mime.setText(item.data(Qt.ItemDataRole.UserRole) or item.text().split()[0])
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class _QABoard(QWidget):
    """
    Drop target that holds quick-action button cards.
    Each card: action label + ▶ Run button + ✕ remove.
    """
    changed = pyqtSignal()  # emitted whenever cards are added/removed

    _NO_ARG_ACTIONS = frozenset({
        # Time / date
        "TIME", "DATE", "GET_DATE", "GET_TIME",
        # System info
        "SCREENSHOT", "HARDWARE_PROFILE", "LIST_DIR",
        "RUNTIME_AUDIT", "IMPORT_AUDIT", "RESOLVE_RUNTIME_PATHS",
        "GUI_RUNTIME_AUDIT", "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME",
        "RUNTIME_STATUS", "REASONING_MODE_STATUS", "MEMORY_STATUS", "COGNITION_STATUS",
        # Memory
        "MEMORY_STATS", "AWARENESS_STATUS", "PERSONAL_MEMORY_SUMMARY",
        "PERSONAL_MEMORY_DEEP_EXPLAIN", "ROUTING_FAULT_EXPLAIN", "NAME_SOURCE_AUDIT",
        # Help & capabilities
        "HELP", "LIST_CAPABILITIES",
        # Self-awareness / improvement
        "SELF_TEST", "SELF_ANALYZE", "SELF_IMPROVE", "SELF_PATCH", "SELF_UPGRADE",
        "MORNING_REPORT", "CODE_CHANGES",
        # Proactive daemon
        "PROACTIVE_START", "PROACTIVE_STOP", "PROACTIVE_STATUS", "HABIT_STATUS",
        # Clipboard (get)
        "GET_CLIPBOARD",
        # Plugins
        "PLUGIN_LIST", "PLUGIN_STATUS",
        # News (default fetch all)
        "NEWS_FETCH",
        # Misc
        "GET_STATUS", "SHOW_DIFF", "LIST_EVENTS",
        # Media control (no args needed to pause/stop/next/prev)
        "STOP_MEDIA", "PAUSE_MEDIA", "PLAY_MEDIA", "NEXT_MEDIA",
        "PREVIOUS_MEDIA", "SHUFFLE_MEDIA", "REPEAT_MEDIA",
        # Chronal / system
        "CHECK_CHRONAL_ALIGNMENT",
        # File system / OS launchers
        "OPEN_FILE_SYSTEM", "OPEN_AUDIO_SETTINGS", "OPEN_BROWSER",
        "OPEN_COMMUNICATION_HUB", "OPEN_MEDIA_HUB", "OPEN_NETWORK_BROWSER",
        "OPEN_SYSTEM_SETTINGS", "OPEN_POWER_SETTINGS", "OPEN_IDE",
        # Persona
        "PERSONA_LOCK_STATUS", "PERSONA_LOCK_CLEAR",
        # System stats (no args)
        "CPU_USAGE", "RAM_USAGE", "SYSTEM_STATS",
        # Notes / Pomodoro no-arg variants
        "LIST_NOTES", "POMODORO_STATUS", "POMODORO_STOP",
    })

    def __init__(self, execute_fn, parent=None):
        super().__init__(parent)
        self._execute_fn = execute_fn   # callable(action_name, args)
        self._cards: dict[str, QWidget] = {}
        self._flow = _FlowLayout(self, h_spacing=10, v_spacing=10)
        self.setLayout(self._flow)
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)

    # ── Drop handling ────────────────────────────────────────────────────────
    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasText():
            action = event.mimeData().text().strip()
            if action:
                self.add_card(action)
                event.acceptProposedAction()

    # ── Card management ──────────────────────────────────────────────────────
    def add_card(self, action_name: str) -> bool:
        """Add a card for action_name. Returns False if already present."""
        if action_name in self._cards:
            return False

        card = QFrame()
        card.setFixedSize(148, 88)
        card.setStyleSheet(
            "QFrame{background:#1e2230;border:1px solid #3b4060;"
            "border-radius:8px;}"
            "QLabel{background:transparent;color:#c8d0e0;}"
            "QPushButton{border-radius:4px;padding:3px 6px;font-size:10px;}"
        )
        vl = QVBoxLayout(card)
        vl.setContentsMargins(8, 6, 8, 6)
        vl.setSpacing(4)

        # Top row: label + close
        top = QHBoxLayout()
        lbl = QLabel(action_name)
        lbl.setStyleSheet("font-size:10px;font-weight:bold;color:#88c0d0;")
        lbl.setWordWrap(True)
        top.addWidget(lbl, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(
            "QPushButton{background:#3b1f1f;color:#bf616a;border:none;font-size:9px;}"
            "QPushButton:hover{background:#bf616a;color:#fff;}"
        )
        close_btn.clicked.connect(lambda: self.remove_card(action_name))
        top.addWidget(close_btn)
        vl.addLayout(top)

        # Run button
        run_btn = QPushButton("▶  Run")
        run_btn.setStyleSheet(
            "QPushButton{background:#2d4a3e;color:#a3be8c;border:1px solid #3d6b56;}"
            "QPushButton:hover{background:#3d6b56;}"
            "QPushButton:pressed{background:#2a5c48;}"
        )
        run_btn.clicked.connect(lambda: self._fire(action_name))
        vl.addWidget(run_btn)

        self._flow.addWidget(card)
        self._cards[action_name] = card
        self.update()
        self.changed.emit()
        return True

    def remove_card(self, action_name: str):
        card = self._cards.pop(action_name, None)
        if card:
            self._flow.removeWidget(card)
            card.deleteLater()
            self.update()
            self.changed.emit()

    def clear_all(self):
        for name in list(self._cards.keys()):
            self.remove_card(name)

    def action_names(self) -> list:
        return list(self._cards.keys())

    # ── Action execution ─────────────────────────────────────────────────────
    def _fire(self, action_name: str):
        # Pass empty args — _qa_run_action on EliMainWindow handles all
        # arg prompting using _QA_ACTION_ARGS metadata (specific prompts
        # per action with the correct arg key).  No-arg actions just run.
        self._execute_fn(action_name, {})


class _ZoomableSettingsView(QGraphicsView):
    """
    Wraps a widget in a QGraphicsView that supports Ctrl+Scroll zoom.
    The embedded widget auto-sizes to the viewport width at the current zoom.
    """
    def __init__(self, widget: QWidget, parent=None):
        super().__init__(parent)
        self._inner = widget
        self._zoom = 1.0

        scene = QGraphicsScene(self)
        self._proxy = QGraphicsProxyWidget()
        self._proxy.setWidget(widget)
        scene.addItem(self._proxy)
        self.setScene(scene)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#1a1d23")))
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_inner()

    def _fit_inner(self):
        vw = max(100, self.viewport().width())
        target_w = max(100, int(vw / self._zoom))
        self._inner.setFixedWidth(target_w)
        self.setSceneRect(0, 0, vw / self._zoom,
                          max(self._proxy.boundingRect().height(),
                              self.viewport().height() / self._zoom))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.08 if delta > 0 else (1.0 / 1.08)
            new_zoom = max(0.55, min(2.0, self._zoom * factor))
            if abs(new_zoom - self._zoom) > 0.001:
                change = new_zoom / self._zoom
                self._zoom = new_zoom
                self.scale(change, change)
                self._fit_inner()
            event.accept()
        else:
            super().wheelEvent(event)

    def zoom_reset(self):
        self.resetTransform()
        self._zoom = 1.0
        self._fit_inner()

    def zoom_in(self):
        new_zoom = min(2.0, self._zoom * 1.08)
        if abs(new_zoom - self._zoom) > 0.001:
            change = new_zoom / self._zoom
            self._zoom = new_zoom
            self.scale(change, change)
            self._fit_inner()

    def zoom_out(self):
        new_zoom = max(0.55, self._zoom / 1.08)
        if abs(new_zoom - self._zoom) > 0.001:
            change = new_zoom / self._zoom
            self._zoom = new_zoom
            self.scale(change, change)
            self._fit_inner()


class _MiniTelemetryGraph(QWidget):
    """Small sparkline-style history graph for live runtime telemetry."""

    def __init__(self, accent: str = "#88c0d0", parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self._accent = QColor(accent)
        self.setMinimumHeight(72)

    def set_values(self, values: List[float]):
        self._values = [max(0.0, min(100.0, float(v))) for v in values]
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(1, 1, -1, -1)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        p.setPen(QPen(QColor("#283142"), 1))
        p.setBrush(QColor("#11151d"))
        p.drawRoundedRect(rect, 6, 6)

        if len(self._values) < 2:
            p.setPen(QColor("#5f6f86"))
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "Waiting for samples...")
            return

        path = QPainterPath()
        area = QPainterPath()
        x_step = rect.width() / max(1, len(self._values) - 1)
        bottom = rect.bottom()

        points: list[QPointF] = []
        for idx, value in enumerate(self._values):
            x = rect.left() + (idx * x_step)
            y = rect.bottom() - ((value / 100.0) * rect.height())
            points.append(QPointF(x, y))

        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)

        area.moveTo(rect.left(), bottom)
        for pt in points:
            area.lineTo(pt)
        area.lineTo(rect.right(), bottom)
        area.closeSubpath()

        fill = QColor(self._accent)
        fill.setAlpha(58)
        p.setPen(QPen(self._accent, 2))
        p.setBrush(fill)
        p.drawPath(area)
        p.drawPath(path)


class _ZoomableImagePreview(QScrollArea):
    """Scrollable image preview with Ctrl+wheel and button-driven zoom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 1.0
        self._pixmap = QPixmap()
        self._label = QLabel("Generate an image to preview it here.")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color:#73839a;font-size:13px;padding:30px;")
        self._label.setMinimumSize(420, 320)
        self.setWidget(self._label)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QScrollArea{background:#10141d;border:1px solid #283142;border-radius:10px;}"
        )

    def set_image_path(self, path: str | Path):
        pix = QPixmap(str(path))
        if pix.isNull():
            self._label.setText(f"Unable to load preview:\n{path}")
            self._pixmap = QPixmap()
            return False
        self._pixmap = pix
        self._zoom = 1.0
        self._render()
        return True

    def _render(self):
        if self._pixmap.isNull():
            return
        target = self._pixmap.size() * self._zoom
        scaled = self._pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_in(self):
        self._zoom = min(6.0, self._zoom * 1.12)
        self._render()

    def zoom_out(self):
        self._zoom = max(0.2, self._zoom / 1.12)
        self._render()

    def zoom_reset(self):
        self._zoom = 1.0
        self._render()


class _GlobalScrollZoom(QObject):
    """Intercepts Ctrl+Wheel app-wide and zooms the application font.

    Scaling the QApplication font cascades to every widget that hasn't
    locked its own point size, so the whole UI grows/shrinks consistently
    instead of relying on per-widget zoomIn/zoomOut (which only QTextEdit
    and a handful of others support and which previously left half the
    GUI unscaled).
    """

    _MIN_POINT = 7
    _MAX_POINT = 28

    def eventFilter(self, obj, event):
        try:
            if event.type() != QEvent.Type.Wheel:
                return False
            mods = event.modifiers()
            if not (mods & Qt.KeyboardModifier.ControlModifier):
                return False

            angle = event.angleDelta().y() if hasattr(event, "angleDelta") else 0
            if angle == 0 and hasattr(event, "pixelDelta"):
                angle = event.pixelDelta().y()
            if angle == 0:
                return False
            step = 1 if angle > 0 else -1

            app = QApplication.instance()
            if app is not None:
                f = app.font()
                cur = f.pointSize() if f.pointSize() > 0 else 9
                new_size = max(self._MIN_POINT, min(self._MAX_POINT, cur + step))
                if new_size != cur:
                    f.setPointSize(new_size)
                    app.setFont(f)
                    # Force every top-level widget to repolish so children pick up
                    # the new application font (some Qt styles cache the old size).
                    for w in app.topLevelWidgets():
                        try:
                            w.setFont(f)
                            for child in w.findChildren(QWidget):
                                if child.font().pointSize() == cur or child.font().pointSize() <= 0:
                                    cf = child.font()
                                    cf.setPointSize(new_size)
                                    child.setFont(cf)
                        except Exception:
                            pass
                        # Dark theme's stylesheet pins font sizes; regenerate it
                        # at the new size so zoom works on BOTH themes.
                        try:
                            if hasattr(w, "apply_theme"):
                                w.apply_theme()
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
            event.accept()
            return True
        except Exception:
            pass
        return False

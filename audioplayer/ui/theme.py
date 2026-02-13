from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def qss_rgba(color: QColor, alpha: int) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def system_prefers_dark(widget) -> bool:
    try:
        hints = widget.styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:  # noqa: BLE001
        pass

    window_color = widget.palette().window().color()
    return window_color.lightness() < 128


def resolve_playhead_color(playhead_color: str, effective_theme: str, accent: QColor) -> QColor:
    custom_color = QColor(playhead_color)
    if custom_color.isValid():
        return custom_color
    if effective_theme == "light":
        return QColor(accent).darker(160)
    return QColor(accent).lighter(170)


def make_playhead_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color)
    pen.setWidthF(max(1.0, min(float(width), 6.0)))
    pen.setCosmetic(True)
    return pen


def build_dark_style(accent: QColor) -> str:
    checked_bg = accent.darker(210)
    checked_border = accent.darker(165)
    list_selected = accent.darker(200)
    list_selected_border = qss_rgba(accent.darker(135), 195)
    icon_checked = accent.darker(185)
    button_bg = qss_rgba(accent, 36)
    button_border = qss_rgba(accent, 135)
    button_hover = qss_rgba(accent, 62)
    button_pressed = qss_rgba(accent, 86)
    list_border = qss_rgba(accent, 88)
    tab_bg = "#252628"
    tab_hover_bg = "#2f3135"
    tab_inactive_text = "#c4cfdb"
    tab_active_text = "#f4f8ff"
    tab_selected_bg = qss_rgba(accent, 76)
    tab_selected_border = qss_rgba(accent, 170)
    return f"""
QWidget {{
    background: #1f1f1f;
    color: #d4d4d4;
    font-family: "SF Pro Display", "Avenir Next", "Helvetica Neue", sans-serif;
    font-size: 12px;
}}
QMainWindow {{
    background: #181818;
}}
QToolBar {{
    spacing: 6px;
    background: #181818;
    border-bottom: 1px solid #2c2c2c;
    padding: 6px;
}}
QToolBar#MainToolbar > QWidget {{
    background: transparent;
    border: none;
}}
QToolBar::separator {{
    width: 0px;
    border: none;
    margin: 0px;
    padding: 0px;
    background: transparent;
}}
QWidget#ToolbarContent {{
    background: transparent;
    border: none;
}}
QWidget#ToolbarSection {{
    background: transparent;
    border: none;
}}
QPushButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #f3f3f3;
}}
QPushButton:hover {{
    background: {button_hover};
}}
QPushButton:pressed {{
    background: {button_pressed};
}}
QPushButton:checked {{
    background: {checked_bg.name()};
    border: 1px solid {checked_border.name()};
}}
QListWidget {{
    background: #1b1b1b;
    border: 1px solid {list_border};
    border-radius: 10px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px;
    border-radius: 8px;
}}
QListWidget::item:selected {{
    background: {qss_rgba(list_selected, 220)};
    color: #f4f8ff;
    border: 1px solid {list_selected_border};
}}
QListWidget::item:selected:!active {{
    background: {qss_rgba(list_selected, 220)};
    color: #f4f8ff;
    border: 1px solid {list_selected_border};
}}
QFrame#InfoCard {{
    background: #1b1b1b;
    border: 1px solid {list_border};
    border-radius: 10px;
}}
QTabWidget::pane {{
    border: 1px solid #30363d;
    border-radius: 8px;
    top: -1px;
    padding: 8px;
    background: #1b1b1b;
}}
QTabBar::tab {{
    background: {tab_bg};
    color: {tab_inactive_text};
    border: 1px solid #3a4047;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 5px 10px;
    margin-right: 4px;
    min-width: 92px;
}}
QTabBar::tab:!selected:hover {{
    background: {tab_hover_bg};
    color: #dde7f3;
}}
QTabBar::tab:selected {{
    background: {tab_selected_bg};
    color: {tab_active_text};
    border: 1px solid {tab_selected_border};
}}
QToolButton#ThemeButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 16px;
    padding: 5px;
}}
QToolButton#ThemeButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #f3f3f3;
}}
QToolButton#ToolbarButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton::menu-indicator {{
    background: transparent;
    border: none;
    image: none;
    width: 0px;
}}
QPushButton#IconControl {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QPushButton#IconControl:hover {{
    background: #343434;
    border-radius: 6px;
}}
QPushButton#IconControl:checked {{
    background: {qss_rgba(icon_checked, 210)};
    border-radius: 6px;
}}
"""


def build_light_style(accent: QColor) -> str:
    checked_bg = accent.lighter(170)
    checked_border = accent.lighter(130)
    list_selected = accent.lighter(175)
    list_selected_border = qss_rgba(accent.darker(120), 165)
    icon_checked = accent.lighter(170)
    button_bg = qss_rgba(accent, 45)
    button_border = qss_rgba(accent.darker(120), 132)
    button_hover = qss_rgba(accent, 75)
    button_pressed = qss_rgba(accent, 102)
    list_border = qss_rgba(accent.darker(120), 92)
    tab_bg = "#e8f0fb"
    tab_hover_bg = "#ddeafc"
    tab_inactive_text = "#2e4966"
    tab_active_text = "#0f243b"
    tab_selected_bg = qss_rgba(accent, 115)
    tab_selected_border = qss_rgba(accent.darker(120), 150)
    return f"""
QWidget {{
    background: #f4f7fb;
    color: #17212f;
    font-family: "SF Pro Display", "Avenir Next", "Helvetica Neue", sans-serif;
    font-size: 12px;
}}
QMainWindow {{
    background: #edf2f8;
}}
QToolBar {{
    spacing: 6px;
    background: #f6f9ff;
    border-bottom: 1px solid #cbd9ec;
    padding: 6px;
}}
QToolBar#MainToolbar > QWidget {{
    background: transparent;
    border: none;
}}
QToolBar::separator {{
    width: 0px;
    border: none;
    margin: 0px;
    padding: 0px;
    background: transparent;
}}
QWidget#ToolbarContent {{
    background: transparent;
    border: none;
}}
QWidget#ToolbarSection {{
    background: transparent;
    border: none;
}}
QPushButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #13253a;
}}
QPushButton:hover {{
    background: {button_hover};
}}
QPushButton:pressed {{
    background: {button_pressed};
}}
QPushButton:checked {{
    background: {checked_bg.name()};
    border: 1px solid {checked_border.name()};
}}
QListWidget {{
    background: #ffffff;
    border: 1px solid {list_border};
    border-radius: 10px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px;
    border-radius: 8px;
}}
QListWidget::item:selected {{
    background: {qss_rgba(list_selected, 220)};
    color: #0f243b;
    border: 1px solid {list_selected_border};
}}
QListWidget::item:selected:!active {{
    background: {qss_rgba(list_selected, 220)};
    color: #0f243b;
    border: 1px solid {list_selected_border};
}}
QFrame#InfoCard {{
    background: #ffffff;
    border: 1px solid {list_border};
    border-radius: 10px;
}}
QTabWidget::pane {{
    border: 1px solid #c5d5e8;
    border-radius: 8px;
    top: -1px;
    padding: 8px;
    background: #ffffff;
}}
QTabBar::tab {{
    background: {tab_bg};
    color: {tab_inactive_text};
    border: 1px solid #c5d5e8;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 5px 10px;
    margin-right: 4px;
    min-width: 92px;
}}
QTabBar::tab:!selected:hover {{
    background: {tab_hover_bg};
    color: #19334d;
}}
QTabBar::tab:selected {{
    background: {tab_selected_bg};
    color: {tab_active_text};
    border: 1px solid {tab_selected_border};
}}
QToolButton#ThemeButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 16px;
    padding: 5px;
}}
QToolButton#ThemeButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #13253a;
}}
QToolButton#ToolbarButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton::menu-indicator {{
    background: transparent;
    border: none;
    image: none;
    width: 0px;
}}
QPushButton#IconControl {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QPushButton#IconControl:hover {{
    background: #d9e6f8;
    border-radius: 6px;
}}
QPushButton#IconControl:checked {{
    background: {qss_rgba(icon_checked, 220)};
    border-radius: 6px;
}}
"""


def build_repeat_mode_icon(mode: str, button_text_color: QColor) -> QIcon:
    pix = QPixmap(20, 20)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen_color = QColor(button_text_color)
    if mode == "off":
        pen_color.setAlpha(110)
    base_pen = QPen(pen_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(base_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    top_path = QPainterPath()
    top_path.moveTo(4, 8)
    top_path.cubicTo(4, 4, 8, 3, 11, 3)
    top_path.lineTo(16, 3)
    painter.drawPath(top_path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(pen_color)
    head_top = QPainterPath()
    head_top.moveTo(16, 3)
    head_top.lineTo(13.0, 1.0)
    head_top.lineTo(13.0, 5.0)
    head_top.closeSubpath()
    painter.drawPath(head_top)
    painter.setPen(base_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    bottom_path = QPainterPath()
    bottom_path.moveTo(16, 12)
    bottom_path.cubicTo(16, 16, 12, 17, 9, 17)
    bottom_path.lineTo(4, 17)
    painter.drawPath(bottom_path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(pen_color)
    head_bottom = QPainterPath()
    head_bottom.moveTo(4, 17)
    head_bottom.lineTo(7.0, 15.0)
    head_bottom.lineTo(7.0, 19.0)
    head_bottom.closeSubpath()
    painter.drawPath(head_bottom)
    painter.setPen(base_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if mode == "one":
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(9.0)
        painter.setFont(font)
        painter.setPen(QPen(pen_color, 1.4))
        painter.drawText(8, 13, "1")
    painter.end()
    return QIcon(pix)


def build_auto_next_icon(enabled: bool, button_text_color: QColor) -> QIcon:
    pix = QPixmap(20, 20)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fg = QColor(button_text_color)
    dim = QColor(fg)
    dim.setAlpha(95)
    active = fg if enabled else dim
    arc_pen = QPen(active, 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(arc_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(2, 2, 16, 16, 30 * 16, 300 * 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(active)
    head = QPainterPath()
    head.moveTo(16.0, 8.0)
    head.lineTo(18.6, 5.3)
    head.lineTo(13.5, 7.0)
    head.closeSubpath()
    painter.drawPath(head)

    painter.setBrush(active)
    play_path = QPainterPath()
    play_path.moveTo(8, 6)
    play_path.lineTo(14, 10)
    play_path.lineTo(8, 14)
    play_path.closeSubpath()
    painter.drawPath(play_path)
    painter.end()
    return QIcon(pix)


def build_follow_icon(enabled: bool, button_text_color: QColor) -> QIcon:
    pix = QPixmap(20, 20)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fg = QColor(button_text_color)
    dim = QColor(fg)
    dim.setAlpha(100)
    color = fg if enabled else dim
    pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(4, 4, 12, 12)
    painter.drawLine(10, 2, 10, 6)
    painter.drawLine(10, 14, 10, 18)
    painter.drawLine(2, 10, 6, 10)
    painter.drawLine(14, 10, 18, 10)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(8, 8, 4, 4)
    painter.end()
    return QIcon(pix)


def build_sun_icon() -> QIcon:
    pix = QPixmap(20, 20)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#0f1115"), 1.8))
    center_x = 10
    center_y = 10
    for dx, dy in ((0, -7), (0, 7), (-7, 0), (7, 0), (-5, -5), (5, -5), (-5, 5), (5, 5)):
        painter.drawLine(center_x + int(dx * 0.75), center_y + int(dy * 0.75), center_x + dx, center_y + dy)
    painter.setPen(QPen(QColor("#0f1115"), 1.4))
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(5, 5, 10, 10)
    painter.end()
    return QIcon(pix)


def build_moon_icon() -> QIcon:
    pix = QPixmap(20, 20)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(3, 3, 14, 14)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawEllipse(8, 2, 11, 15)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.setPen(QPen(QColor("#ffffff"), 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(3, 3, 14, 14)
    painter.end()
    return QIcon(pix)

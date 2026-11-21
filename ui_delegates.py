# ui_delegates.py
from PyQt5.QtCore import QSize, QRect, Qt
from PyQt5.QtGui import QTextLayout, QTextOption, QFontMetrics, QPalette, QBrush, QColor

from PyQt5.QtWidgets import (
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QApplication,
)


class TwoLineDelegate(QStyledItemDelegate):
    """单元格文字最多 max_lines 行，超出的在最后一行末尾用省略号。
    行高固定（uniformRowHeights 仍可开启），保证大列表滚动性能。"""

    def __init__(self, parent=None, max_lines=2, min_height=0, pad=2,
                 elide_modes=None, default_elide=Qt.ElideRight):
        super().__init__(parent)
        self.max_lines = max_lines
        self.min_height = min_height
        self.pad = pad
        # 列号 -> 省略方向，例如 {1: Qt.ElideLeft}
        self.elide_modes = elide_modes or {}
        self.default_elide = default_elide


    def _line_ranges(self, text, width, font):
        """返回每行的 (start, length)，最多多取一行用于判断是否有剩余"""
        layout = QTextLayout(text, font)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)  # 无空格地图名也能断
        layout.setTextOption(opt)
        layout.beginLayout()
        ranges = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            ranges.append((line.textStart(), line.textLength()))
            if len(ranges) > self.max_lines:
                break
        layout.endLayout()
        return ranges

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        fm = QFontMetrics(option.font)
        h = max(self.min_height, fm.lineSpacing() * self.max_lines + self.pad * 2)
        return QSize(base.width(), max(base.height(), h))

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        if not text:
            super().paint(painter, option, index)
            return

        style = opt.widget.style() if opt.widget else QApplication.style()

        # 先让样式画背景 / 选中态 / 图标，文字留空自己画
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        rect = rect.adjusted(2, 0, -2, 0)
        if rect.width() <= 0:
            return

        ranges = self._line_ranges(text, rect.width(), opt.font)
        has_more = len(ranges) > self.max_lines
        ranges = ranges[: self.max_lines]

        # 尊重 item.setForeground() 设置的颜色（非 DDNet 地图的红色小字靠它）
        fg = index.data(Qt.ForegroundRole)
        fg_color = None
        if isinstance(fg, QBrush):
            fg_color = fg.color()
        elif isinstance(fg, QColor):
            fg_color = fg

        fm = QFontMetrics(opt.font)
        painter.save()
        painter.setFont(opt.font)
        if opt.state & QStyle.State_Selected:
            painter.setPen(opt.palette.color(QPalette.HighlightedText))
        elif fg_color is not None:
            painter.setPen(fg_color)
        else:
            painter.setPen(opt.palette.color(QPalette.Text))

        total_h = fm.lineSpacing() * len(ranges)
        y = rect.top() + max(0, (rect.height() - total_h) // 2)
        for i, (start, length) in enumerate(ranges):
            seg = text[start : start + length]
            if i == len(ranges) - 1 and has_more:
                mode = self.elide_modes.get(index.column(), self.default_elide)
                seg = fm.elidedText(text[start:], mode, rect.width())
            painter.drawText(
                QRect(rect.left(), y, rect.width(), fm.lineSpacing()),
                Qt.AlignLeft | Qt.AlignVCenter,
                seg,
            )
            y += fm.lineSpacing()
        painter.restore()

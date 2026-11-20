# -*- coding: utf-8 -*-
"""UI 管理器：对话气泡 + 信息卡片 + 设置窗口 + 系统托盘

- BubbleWidget:   漫画风格气泡（白色圆角 + 小三角尾巴，自动淡出）
- InfoCardWidget: 搜索结果卡片（名称/路径/大小，可手动关闭或自动关闭）
- SettingsDialog: 性格切换 / 开机自启 / 主动互动开关 / 退出
- TrayIcon:       托盘图标（右键退出）
"""

from PyQt5.QtCore import Qt, QEvent, QRect, QTimer, QPropertyAnimation, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap, QPainterPath, QTextDocument
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
    QCheckBox, QSystemTrayIcon, QMenu, QAction, QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect, QApplication, QSlider, QLineEdit,
    QScrollArea, QFrame,
)

from framework import config
from framework.utils import plain_text, resource_path, logger
from . import theme
from .icons import icon_label, pixmap


# ============================================================
# 漫画对话气泡
# ============================================================
class BubbleWidget(QWidget):
    """角色头顶的漫画气泡：显示 3~4 秒后自动淡出，出现时带气泡音"""

    MAX_TEXT_WIDTH = theme.DIM["float_bubble_text_w"]   # 文本区最大宽度（超宽自动换行）

    def __init__(self, parent=None, sound=None):
        super().__init__(parent)
        self.sound = sound
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        panel = QWidget(self)
        panel.setObjectName("bubblePanel")
        panel_layout = QVBoxLayout(panel)
        m = theme.SP
        panel_layout.setContentsMargins(m["xl"], m["m"], m["xl"], m["m"])
        self._label = QLabel(panel)
        self._label.setObjectName("bubbleLabel")
        self._label.setWordWrap(True)
        panel_layout.addWidget(self._label)

        tail_h = self._tail_h = m["m"]   # 底部三角尾巴高度
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, tail_h)
        outer.addWidget(panel)

        # 淡出动画
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(600)

        # 自动消失定时器
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

        # 手动算尺寸时需要的边距：直接从上面两个布局读，避免两处数字各写一遍
        pm = panel_layout.contentsMargins()
        self._pad_lr = pm.left() + pm.right()
        self._pad_tb = pm.top() + pm.bottom() + tail_h
        # 样式由 ui/theme.py 装到 QApplication 上统一生效，这里不再单独加载

    # ------------------------------------------------------------
    def show_text(self, text: str, pet_x: int, pet_y: int):
        """显示气泡文本，锚定在角色头顶"""
        text = plain_text(text)
        self._label.setText(text)
        self._resize_for_text(text)
        self._place_above(pet_x, pet_y)
        self._opacity.setOpacity(1.0)
        self.show()
        self.raise_()
        if self.sound:
            self.sound.play("bubble")
        self._hide_timer.start(self._duration_for(text))

    @staticmethod
    def _duration_for(text: str) -> int:
        """按字数决定停留时长：基础 3.5 秒 + 每字 180ms，封顶 30 秒，
        让用户来得及看完长回复而不用去翻聊天记录"""
        chars = sum(1 for c in text if not c.isspace())
        return min(config.BUBBLE_DURATION + chars * config.BUBBLE_MS_PER_CHAR,
                   config.BUBBLE_DURATION_MAX)

    def _resize_for_text(self, text: str):
        """按文本实际换行结果手动计算气泡宽高。

        Qt 的 wordWrap + adjustSize 有布局缺陷：多行长文本会被压成
        "窄高条"。这里用字体度量先算出换行后的真实宽高，再定死尺寸。
        """
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)   # 解除上一条的 fixed 限制
        self._label.setMinimumSize(0, 0)
        self._label.setMaximumSize(16777215, 16777215)

        self._label.ensurePolished()              # 让 QSS 字体先生效
        fm = self._label.fontMetrics()
        lines = text.split("\n")
        natural = max((fm.horizontalAdvance(ln) for ln in lines),
                      default=0)
        if natural <= self.MAX_TEXT_WIDTH:
            # 短文本：不换行，一行一行排
            w = max(natural, 24)
        else:
            # 长文本：限宽换行，宽度取换行后最宽那一行
            rect = fm.boundingRect(0, 0, self.MAX_TEXT_WIDTH, 1000000,
                                   Qt.TextWordWrap, text)
            w = rect.width()
        # 高度改用 QTextDocument 精确测量：boundingRect / fm.height 对多行
        # 文本少算行间距，长回复最后几行会被气泡底边拦腰截断。
        doc = QTextDocument()
        doc.setDefaultFont(self._label.font())
        doc.setDocumentMargin(0)
        doc.setTextWidth(max(1, w))
        doc.setPlainText(text)
        h = int(doc.size().height())
        # 2px 冗余防边缘裁切；手动定死窗口尺寸——不能走 adjustSize()，
        # 它读到的是布局缓存的旧 sizeHint（布局失效事件要等事件循环），
        # 会导致气泡尺寸滞后一轮、长文本被压成上一条的形状
        self._label.setFixedSize(w + 2, h + 2)
        self.setFixedSize(w + 2 + self._pad_lr, h + 2 + self._pad_tb)

    def follow(self, pet_x: int, pet_y: int):
        """角色移动时跟随"""
        if self.isVisible():
            self._place_above(pet_x, pet_y)

    def _place_above(self, pet_x: int, pet_y: int):
        """放置在角色头顶（超出屏幕右边界时自动内收）"""
        screen = QApplication.primaryScreen().availableGeometry()
        x = pet_x + config.PET_SIZE // 2 - self.width() // 2
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        y = pet_y - self.height() - 6
        if y < screen.top():
            y = pet_y + config.PET_SIZE + 6
        self.move(x, y)

    def _fade_out(self):
        """淡出后隐藏"""
        self._fade.stop()
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        try:
            self._fade.finished.disconnect()
        except TypeError:
            pass
        self._fade.finished.connect(self.hide)
        self._fade.start()

    def paintEvent(self, event):
        """绘制指向角色的三角尾巴：填浮层底色，只描两条斜边，不压住面板上沿"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        base, half = h - self._tail_h, 10
        left, right, tip = (w / 2 - half, base), (w / 2 + half, base), (w / 2, h - 1)
        fill = QPainterPath()
        fill.moveTo(*left)
        fill.lineTo(*tip)
        fill.lineTo(*right)
        fill.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.Q("FLOAT_BG"))
        painter.drawPath(fill)
        edge = QPainterPath()
        edge.moveTo(*left)
        edge.lineTo(*tip)
        edge.lineTo(*right)
        painter.setPen(theme.Q("FLOAT_BORDER"))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(edge)


# ============================================================
# 信息卡片
# ============================================================
class InfoCardWidget(QWidget):
    """搜索结果信息卡片：应用名称 + 完整路径 + 文件大小 + 关闭按钮

    拉出卡片时播放"抽卡音 + 气泡音"（声音管理器可选传入）。
    """

    def __init__(self, parent=None, sound=None):
        super().__init__(parent)
        self.sound = sound
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        panel = QWidget(self)
        panel.setObjectName("cardPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(theme.SP["l"], theme.SP["m"],
                                  theme.SP["m"], theme.SP["m"])
        layout.setSpacing(theme.SP["s"])

        # 标题行：矢量图标 + 名称 + 关闭按钮
        top = QHBoxLayout()
        self._icon = QLabel(panel)
        self._icon.setObjectName("cardIcon")
        self._icon.setFixedSize(20, 20)
        top.addWidget(self._icon)
        top.addSpacing(theme.SP["xs"] if "xs" in theme.SP else 6)
        self._title = QLabel(panel)
        self._title.setObjectName("cardTitleFloat")
        top.addWidget(self._title)
        top.addStretch()
        close_btn = QPushButton("×", panel)
        close_btn.setObjectName("cardClose")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        top.addWidget(close_btn)
        layout.addLayout(top)
        self._top = top

        # 路径 + 大小
        self._path = QLabel(panel)
        self._path.setObjectName("cardPath")
        self._path.setWordWrap(True)
        self._path.setMaximumWidth(theme.DIM["card_path_w"])
        layout.addWidget(self._path)

        self._size = QLabel(panel)
        self._size.setObjectName("cardSize")
        layout.addWidget(self._size)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP["m"], theme.SP["m"],
                                 theme.SP["m"], theme.SP["m"])
        outer.addWidget(panel)
        # 横向 chrome = 外层留白 + 面板留白；从布局读回，改令牌不会算错
        _o_l, _, _o_r, _ = outer.getContentsMargins()
        _p_l, _, _p_r, _ = layout.getContentsMargins()
        self._chrome_w = _o_l + _o_r + _p_l + _p_r

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 4)
        shadow.setColor(theme.Q("SHADOW"))
        panel.setGraphicsEffect(shadow)

        # 自动关闭（5~8 秒）
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.close)

    # ------------------------------------------------------------
    def show_card(self, title: str, path: str = "", size: str = "",
                  near_x: int = None, near_y: int = None, icon: str = ""):
        """展示卡片：出现在屏幕右下角或角色旁边（icon 为矢量线框图标名）"""
        self._title.setText(title)
        if icon:
            self._icon.setPixmap(pixmap(icon, theme.T("ACCENT"), 20))
            self._icon.setVisible(True)
        else:
            self._icon.setVisible(False)
        self._path.setText(path or "—")
        self._size.setText(size or "")
        self._resize_path(self._path.text())

        screen = QApplication.primaryScreen().availableGeometry()
        if near_x is not None:
            x = near_x + config.PET_SIZE + 20
            y = near_y
            if x + self.width() > screen.right():
                x = near_x - self.width() - 20
        else:
            x = screen.right() - self.width() - 30
            y = screen.bottom() - self.height() - 30
        self.move(max(screen.left(), x), max(screen.top(), y))

        self.show()
        self.raise_()
        # 拉出卡片：抽卡音 + 气泡音
        if self.sound:
            self.sound.play("card")
        self._auto_timer.start(config.CARD_DURATION)

    def _resize_path(self, path: str):
        """路径标签限宽换行手动算尺寸（避开 wordWrap+adjustSize 的压条缺陷）

        宽度还要单独保证容得下标题行：标题不换行，一旦卡片只按路径算宽，
        短路径 + 长文件名就会把标题压成一条缝（实测 157px 的字只剩 17px）。
        """
        max_w = theme.DIM["card_path_w"]
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._path.setMinimumSize(0, 0)
        self._path.setMaximumSize(16777215, 16777215)
        self._path.ensurePolished()
        fm = self._path.fontMetrics()
        if fm.horizontalAdvance(path) <= max_w:
            self._path.setFixedSize(fm.horizontalAdvance(path) + 2,
                                    fm.height() + 2)
        else:
            rect = fm.boundingRect(0, 0, max_w, 1000000,
                                   Qt.TextWordWrap, path)
            self._path.setFixedSize(rect.width() + 2, rect.height() + 2)
        # 标题行需要的最小宽：让布局自己算（标题 + 两处行间距 + 关闭按钮），
        # 再补上横向留白。手工数间距会漏掉 stretch 两侧各一份。
        need_w = self._top.sizeHint().width() + self._chrome_w
        self.layout().activate()
        w = max(self._path.width() + self._chrome_w, need_w)
        self.setFixedSize(w, self.layout().sizeHint().height())


# ============================================================
# 设置窗口
# ============================================================
class SettingsDialog(QWidget):
    """设置窗口：性格切换 + 开机自启 + 主动互动 + 退出

    窗口尺寸由内容决定（不再写死 360x620），弹出时位置会夹紧到屏幕内，
    屏幕矮的时候内容可滚动——任何缩放比/分辨率下都不会有设置项看不到。
    """

    personality_changed = pyqtSignal(str)
    autostart_changed = pyqtSignal(bool)
    proactive_changed = pyqtSignal(bool)
    sound_changed = pyqtSignal(bool)
    doc_changed = pyqtSignal(bool)                  # 允许凌零创建文件
    pexels_key_changed = pyqtSignal(str)            # 图库 Key（联网配图）
    vision_config_changed = pyqtSignal(str, str, str)   # 视觉key/url/model
    websearch_key_changed = pyqtSignal(str)         # 搜索 Key（联网调研）
    volume_changed = pyqtSignal(float)
    ai_config_changed = pyqtSignal(str, str, str)   # api_key, base_url, model
    test_ai_requested = pyqtSignal(str, str, str)   # 测试连接
    lora_config_changed = pyqtSignal(bool, str, str, str)  # 开关,基座,适配器,设备
    test_lora_requested = pyqtSignal(str, str, str)        # 测试本地微调加载
    exit_requested = pyqtSignal()

    MIN_WIDTH = 360              # 窗口最小宽度
    MAX_WIDTH = 520              # 窗口最大宽度（再宽就挡住半个桌面了）
    SCREEN_HEIGHT_RATIO = 0.88   # 窗口最高占屏幕可用高度的比例，超出部分滚动
    SCREEN_MARGIN = 8            # 与屏幕边缘的最小间距

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("设置")
        self._drag_pos = None

        # 内容装进滚动区：屏幕装不下时可以滚，设置项不会被挤出屏幕
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("settingsScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self._scroll)

        panel = QWidget()
        panel.setObjectName("settingsPanel")
        self._scroll.setWidget(panel)
        self._panel = panel
        panel.installEventFilter(self)      # 面板空白处按下也能拖动整窗
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(theme.SP["xxl"], theme.SP["l"],
                                  theme.SP["xxl"], theme.SP["l"])
        layout.setSpacing(theme.SP["m"])

        # ---- 标题栏：标题 + 右上角关闭按钮 ----
        title_row = QHBoxLayout()
        title = QLabel("设置")
        title.setObjectName("settingsTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        close_btn = QPushButton("×")
        close_btn.setObjectName("settingsClose")
        close_btn.setFixedSize(26, 26)
        close_btn.setToolTip("关闭设置（Esc）")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        # ================= AI 配置 =================
        ai_icon = icon_label("robot", theme.T("ACCENT"), 17)
        ai_title = QLabel("DeepSeek AI 配置")
        ai_title.setObjectName("settingsTitle")
        ai_row = QHBoxLayout()
        ai_row.setSpacing(theme.SP["s"])
        ai_row.addWidget(ai_icon)
        ai_row.addWidget(ai_title, 1)
        layout.addLayout(ai_row)

        # API Key（可切换明文/密文）
        layout.addWidget(QLabel("API Key："))
        key_row = QHBoxLayout()
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.Password)
        self._api_key.setPlaceholderText("sk-xxxxxxxxxxxxxxxx")
        self._api_key.setText(config.DEEPSEEK_API_KEY)
        self._eye = QPushButton("显示")
        self._eye.setObjectName("miniButton")
        self._eye.setFixedWidth(52)
        self._eye.setCheckable(True)
        self._eye.toggled.connect(self._toggle_key_visible)
        key_row.addWidget(self._api_key)
        key_row.addWidget(self._eye)
        layout.addLayout(key_row)

        # 接口地址
        layout.addWidget(QLabel("接口地址："))
        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText("https://api.deepseek.com")
        self._base_url.setText(config.DEEPSEEK_BASE_URL)
        layout.addWidget(self._base_url)

        # 模型选择（下拉 + 可手输自定义）
        layout.addWidget(QLabel("模型种类："))
        self._model = QComboBox()
        self._model.setEditable(True)
        self._model.addItems(config.DEEPSEEK_MODELS)
        self._model.setCurrentText(config.DEEPSEEK_MODEL)
        layout.addWidget(self._model)

        # 测试连接 + 状态
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("测试连接")
        self._test_btn.setObjectName("settingsButton")
        self._test_btn.clicked.connect(self._on_test)
        self._ai_status = QLabel("未配置")
        self._ai_status.setObjectName("aiStatus")
        self._ai_status.setWordWrap(True)
        test_row.addWidget(self._test_btn)
        test_row.addWidget(self._ai_status)
        layout.addLayout(test_row)

        # 分隔线：复用全局 QSS 里的 #hline，不再写行内样式
        sep = QFrame()
        sep.setObjectName("hline")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ================= 本地微调模型（LoRA）=================
        # 主链路的第一个 provider：开启后先问本地模型，答得不合协议才退云端。
        lora_icon = icon_label("robot", theme.T("ACCENT"), 17)
        lora_title = QLabel("本地微调模型（LoRA，选填）")
        lora_title.setObjectName("settingsTitle")
        lora_head = QHBoxLayout()
        lora_head.setSpacing(theme.SP["s"])
        lora_head.addWidget(lora_icon)
        lora_head.addWidget(lora_title, 1)
        layout.addLayout(lora_head)

        self._lora_enabled = QCheckBox("使用本地微调模型（没配 Key 也能真对话）")
        self._lora_enabled.setChecked(config.LORA_ENABLED)
        self._lora_enabled.setToolTip(
            "开启后对话先问本地微调模型；答得不合动作协议会退给云端模型")
        layout.addWidget(self._lora_enabled)

        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("基座模型:"))
        self._lora_base = QLineEdit()
        self._lora_base.setPlaceholderText("Qwen/Qwen2.5-1.5B-Instruct")
        self._lora_base.setText(config.LORA_BASE_MODEL)
        base_row.addWidget(self._lora_base)
        layout.addLayout(base_row)

        adapter_row = QHBoxLayout()
        adapter_row.addWidget(QLabel("适配器:"))
        self._lora_adapter = QLineEdit()
        self._lora_adapter.setPlaceholderText("finetune/output")
        self._lora_adapter.setText(config.LORA_ADAPTER_PATH)
        adapter_row.addWidget(self._lora_adapter)
        layout.addLayout(adapter_row)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("设备:"))
        self._lora_device = QComboBox()
        self._lora_device.addItems(["auto", "cpu", "cuda"])
        self._lora_device.setCurrentText(config.LORA_DEVICE)
        device_row.addWidget(self._lora_device)
        layout.addLayout(device_row)

        lora_test_row = QHBoxLayout()
        self._lora_test_btn = QPushButton("测试加载")
        self._lora_test_btn.setObjectName("settingsButton")
        self._lora_test_btn.setToolTip("真的加载一次基座与适配器，看能不能用")
        self._lora_test_btn.clicked.connect(self._on_test_lora)
        self._lora_status = QLabel("已启用 · 未验证" if config.LORA_ENABLED
                                   else "未启用（对话走云端模型）")
        self._lora_status.setObjectName("aiStatus")
        self._lora_status.setWordWrap(True)
        lora_test_row.addWidget(self._lora_test_btn)
        lora_test_row.addWidget(self._lora_status)
        layout.addLayout(lora_test_row)

        sep2 = QFrame()
        sep2.setObjectName("hline")
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFixedHeight(1)
        layout.addWidget(sep2)

        # 性格切换
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("性格："))
        self._persona = QComboBox()
        self._persona.addItems(list(config.PERSONALITIES.keys()))
        self._persona.setCurrentText(config.DEFAULT_PERSONALITY)
        self._persona.currentTextChanged.connect(self.personality_changed.emit)
        row1.addWidget(self._persona)
        layout.addLayout(row1)

        # 开机自启
        self._autostart = QCheckBox("开机自动启动")
        self._autostart.setChecked(config.AUTOSTART_ENABLED)
        self._autostart.toggled.connect(self.autostart_changed.emit)
        layout.addWidget(self._autostart)

        # 主动互动
        self._proactive = QCheckBox("无聊时主动搭话")
        self._proactive.setChecked(config.PROACTIVE_ENABLED)
        self._proactive.toggled.connect(self.proactive_changed.emit)
        layout.addWidget(self._proactive)

        # 文档生成（AI 调用本机工具创建 PPT / Word / Excel / PDF / HTML）
        self._doc = QCheckBox("允许凌零创建文件（PPT/Word/表格/PDF）")
        self._doc.setChecked(config.ALLOW_DOC_GENERATION)
        self._doc.toggled.connect(self.doc_changed.emit)
        layout.addWidget(self._doc)

        # 图库 Key（Pexels，选填）：填了之后凌零做 PPT 会自动联网配图
        pexels_row = QHBoxLayout()
        pexels_row.addWidget(QLabel("图库Key:"))
        self._pexels = QLineEdit()
        self._pexels.setEchoMode(QLineEdit.Password)
        self._pexels.setPlaceholderText("选填，填了做PPT自动配图")
        self._pexels.setText(config.PEXELS_API_KEY)
        pexels_row.addWidget(self._pexels)
        layout.addLayout(pexels_row)

        # 视觉模型（选填）：填了 Key 凌零就能"看"你的屏幕回答问题；
        # 默认智谱 glm-4.6v-flash，想换别的 OpenAI 兼容视觉模型再改下面两项
        vision_row = QHBoxLayout()
        vision_row.addWidget(QLabel("视觉Key:"))
        self._vision = QLineEdit()
        self._vision.setEchoMode(QLineEdit.Password)
        self._vision.setPlaceholderText("选填，填了凌零能看懂你的屏幕")
        self._vision.setText(config.VISION_API_KEY)
        vision_row.addWidget(self._vision)
        layout.addLayout(vision_row)

        self._vision_url = QLineEdit()
        self._vision_url.setPlaceholderText(
            "视觉地址，留空默认智谱 open.bigmodel.cn")
        self._vision_url.setText(config.VISION_API_URL)
        layout.addWidget(self._vision_url)

        self._vision_model = QLineEdit()
        self._vision_model.setPlaceholderText(
            "视觉模型名，留空默认 glm-4.6v-flash")
        self._vision_model.setText(config.VISION_MODEL)
        layout.addWidget(self._vision_model)

        # 搜索 Key（选填）：填了凌零能联网调研，给报告/PPT 收集真实资料
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索Key:"))
        self._websearch = QLineEdit()
        self._websearch.setEchoMode(QLineEdit.Password)
        self._websearch.setPlaceholderText(
            "选填，智谱 Key 即可，填了能联网查资料")
        self._websearch.setText(config.WEBSEARCH_API_KEY)
        search_row.addWidget(self._websearch)
        layout.addLayout(search_row)

        # 音效开关
        self._sound = QCheckBox("音效（气泡音/抽卡音）")
        self._sound.setChecked(config.SOUND_ENABLED)
        self._sound.toggled.connect(self.sound_changed.emit)
        layout.addWidget(self._sound)

        # 音量
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("音量："))
        self._volume = QSlider(Qt.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(int(config.SOUND_VOLUME * 100))
        self._volume.valueChanged.connect(
            lambda v: self.volume_changed.emit(v / 100.0)
        )
        vol_row.addWidget(self._volume)
        layout.addLayout(vol_row)

        layout.addStretch()

        # 按钮
        btns = QHBoxLayout()
        save = QPushButton("保存")
        save.setObjectName("settingsButton")
        save.clicked.connect(self._save)
        self._save_btn = save
        exit_btn = QPushButton("退出")
        exit_btn.setObjectName("exitButton")
        exit_btn.clicked.connect(self.exit_requested.emit)
        btns.addStretch()
        btns.addWidget(save)
        btns.addWidget(exit_btn)
        layout.addLayout(btns)

    # ------------------------------------------------------------
    # 尺寸与位置
    # ------------------------------------------------------------
    @staticmethod
    def _available_screen() -> QRect:
        """当前屏幕可用区域（已排除任务栏）；抽出来便于测试注入小屏幕"""
        return QApplication.primaryScreen().availableGeometry()

    def _fit_to_content(self):
        """按内容量尺寸：宽度夹在 MIN/MAX 之间，高度不超过屏幕可用高度的一定比例。
        超出高度的部分交给滚动区，所以内容再多也不会被切掉。"""
        self._panel.ensurePolished()
        hint = self._panel.sizeHint()
        screen = self._available_screen()

        width = max(self.MIN_WIDTH, min(hint.width(), self.MAX_WIDTH))
        max_height = max(320, int(screen.height() * self.SCREEN_HEIGHT_RATIO))
        height = min(hint.height(), max_height)
        if height < hint.height():
            # 会出现竖直滚动条，把它的宽度补进窗口，免得内容被挤窄
            width = min(width + self._scroll.verticalScrollBar().sizeHint().width(),
                        self.MAX_WIDTH + 24)

        if (width, height) != (self.width(), self.height()):
            self.setFixedSize(width, height)
            self._clamp_into_screen()

    def _clamp_into_screen(self):
        """把窗口夹回屏幕内（可用区域已排除任务栏）；right()/bottom() 是闭区间，故 +1"""
        screen = self._available_screen()
        m = self.SCREEN_MARGIN
        x = max(screen.left() + m,
                min(self.x(), screen.right() + 1 - m - self.width()))
        y = max(screen.top() + m,
                min(self.y(), screen.bottom() + 1 - m - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def show_near(self, anchor: QRect):
        """贴着角色弹出设置窗口。

        早先是按固定偏移摆放，助手默认待在屏幕右下角，设置窗口的右侧和底部
        会跑到屏幕外（实测右下溢出 34px、底部溢出 114px），保存/退出按钮看不见
        也点不到。现在先按内容定尺寸，再夹紧到屏幕内。
        """
        self._fit_to_content()
        screen = self._available_screen()
        x, y = anchor.x() - 80, anchor.y() - 260
        if y + self.height() > screen.bottom() + 1:
            y = anchor.y() + anchor.height() + 16 - self.height()   # 上方放不下就挂到下方
        self.move(x, y)
        self._clamp_into_screen()

        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        """无论从右键菜单还是托盘打开，显示前都按当前内容重新量一次尺寸"""
        self._fit_to_content()
        super().showEvent(event)

    # ------------------------------------------------------------
    # 整窗拖动
    # ------------------------------------------------------------
    def eventFilter(self, obj, event):
        """面板空白处按下也能拖动整窗。

        面板铺满窗口后那圈透明边就不存在了，没有这个兜底就只剩边角可拖。
        落在输入框/下拉框/按钮上的事件由子控件自己吃掉，不会走到这里。
        """
        if obj is self._panel:
            kind = event.type()
            if kind == QEvent.MouseButtonPress:
                self._drag_begin(event)
            elif kind == QEvent.MouseMove:
                self._drag_move(event)
            elif kind in (QEvent.MouseButtonRelease, QEvent.Leave):
                self._drag_pos = None
        return super().eventFilter(obj, event)

    def _drag_begin(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def _drag_move(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            self._clamp_into_screen()      # 拖到屏幕外就贴边停住，窗口跑不丢

    # ------------------------------------------------------------
    # AI 配置交互
    # ------------------------------------------------------------
    def _toggle_key_visible(self, checked: bool):
        """API Key 明文/密文切换"""
        self._api_key.setEchoMode(QLineEdit.Normal if checked
                                  else QLineEdit.Password)
        self._eye.setText("隐藏" if checked else "显示")

    def _current_ai_config(self):
        return (self._api_key.text().strip(),
                self._base_url.text().strip() or config.DEEPSEEK_BASE_URL,
                self._model.currentText().strip() or config.DEEPSEEK_MODEL)

    def _on_test(self):
        """测试连接（结果由外部线程回调设置）"""
        key, url, model = self._current_ai_config()
        self._ai_status.setText("测试中...")
        self.test_ai_requested.emit(key, url, model)

    def _on_test_lora(self):
        """测试本地微调模型能不能加载（结果由外部线程回调设置）"""
        self._lora_status.setText("加载中（可能要几十秒）...")
        self.test_lora_requested.emit(
            self._lora_base.text().strip() or config.LORA_BASE_MODEL,
            self._lora_adapter.text().strip() or config.LORA_ADAPTER_PATH,
            self._lora_device.currentText().strip() or config.LORA_DEVICE)

    def set_lora_status(self, text: str):
        """外部更新本地微调状态文案"""
        self._lora_status.setText(text)
        self._panel.layout().activate()
        self._fit_to_content()

    def set_ai_status(self, text: str):
        """外部更新连接状态文案（文案变长会让窗口长高，不会顶掉底部按钮）"""
        self._ai_status.setText(text)
        self._panel.layout().activate()
        self._fit_to_content()

    def refresh_status(self, status_text: str):
        """打开设置时同步当前 AI 状态"""
        self._ai_status.setText(status_text)

    def refresh_lora_status(self, status_text: str):
        """打开设置时同步当前本地微调状态（只改文字，不动窗口尺寸）"""
        self._lora_status.setText(status_text)

    def _save(self):
        """保存设置（含 AI 配置）"""
        self.personality_changed.emit(self._persona.currentText())
        self.autostart_changed.emit(self._autostart.isChecked())
        self.proactive_changed.emit(self._proactive.isChecked())
        self.doc_changed.emit(self._doc.isChecked())
        self.pexels_key_changed.emit(self._pexels.text().strip())
        self.vision_config_changed.emit(
            self._vision.text().strip(),
            self._vision_url.text().strip() or config.VISION_URL_DEFAULT,
            self._vision_model.text().strip() or config.VISION_MODEL_DEFAULT)
        self.websearch_key_changed.emit(self._websearch.text().strip())
        self.sound_changed.emit(self._sound.isChecked())
        self.volume_changed.emit(self._volume.value() / 100.0)
        self.ai_config_changed.emit(*self._current_ai_config())
        self.lora_config_changed.emit(
            self._lora_enabled.isChecked(),
            self._lora_base.text().strip(),
            self._lora_adapter.text().strip(),
            self._lora_device.currentText().strip())
        # 原来保存后窗口立刻消失，用户分不清是「保存成功」还是「取消/崩了」。
        # 先在按钮上给一句可见结果，再延时关闭，下次打开时按钮已复位。
        self._save_btn.setText("已保存 ✓")
        self._save_btn.setEnabled(False)

        def _finish():
            self._save_btn.setText("保存")
            self._save_btn.setEnabled(True)
            self.close()
        QTimer.singleShot(900, _finish)
        logger.info("[Settings] 设置已保存")

    def keyPressEvent(self, event):
        """Esc 关闭设置窗口（不退出程序）"""
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        """支持拖动设置窗口"""
        self._drag_begin(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._drag_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


# ============================================================
# 系统托盘
# ============================================================
class TrayIcon(QSystemTrayIcon):
    """系统托盘：显示角色图标，右键可退出"""

    exit_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    preview_requested = pyqtSignal()
    workspace_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolTip("桌面AI助手 · 凌零")
        self.setIcon(self._make_icon())

        menu = QMenu()
        act_workspace = QAction("打开工作台（可最大化）", menu)
        act_workspace.triggered.connect(self.workspace_requested.emit)
        act_show = QAction("显示设置", menu)
        act_show.triggered.connect(self.settings_requested.emit)
        act_preview = QAction("助手预览", menu)
        act_preview.triggered.connect(self.preview_requested.emit)
        act_exit = QAction("退出", menu)
        act_exit.triggered.connect(self.exit_requested.emit)
        menu.addAction(act_workspace)
        menu.addAction(act_show)
        menu.addAction(act_preview)
        menu.addSeparator()
        menu.addAction(act_exit)
        self.setContextMenu(menu)

    @staticmethod
    def _make_icon():
        """托盘图标：优先用 assets/icon.png，缺失时程序化生成骑士徽章"""
        import os
        from framework.utils import resource_path
        icon_png = resource_path(os.path.join("assets", "icon.png"))
        if os.path.exists(icon_png):
            from PyQt5.QtGui import QIcon
            return QIcon(icon_png)

        # 骑士徽章兜底：橙色环 + 奶油内圆 + 银灰头盔（配色同 assets/icon）
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(224, 123, 57))
        p.drawEllipse(1, 1, 62, 62)
        p.setBrush(QColor(255, 243, 224))
        p.drawEllipse(5, 5, 54, 54)
        # 盔顶羽饰（红色，先画让盔体压住根部）
        p.setBrush(QColor(214, 88, 68))
        p.drawEllipse(27, 6, 10, 14)
        # 盔体（银灰圆顶）
        p.setBrush(QColor(172, 172, 176))
        p.setPen(QColor(62, 58, 54))
        p.drawEllipse(18, 14, 28, 32)
        # 面甲观察缝
        p.setBrush(QColor(62, 58, 54))
        p.setPen(Qt.NoPen)
        p.drawRect(22, 28, 20, 4)
        # 颈甲
        p.setBrush(QColor(140, 140, 144))
        p.drawRect(21, 42, 22, 5)
        p.end()
        from PyQt5.QtGui import QIcon
        return QIcon(pm)

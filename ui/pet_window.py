# -*- coding: utf-8 -*-
"""主窗口

无边框、透明背景、始终置顶的桌面角色窗口。

交互：
- 单击角色   -> 弹出跟随角色的毛玻璃输入框
- 按住拖拽   -> 角色移动到桌面任意位置
- 右键角色   -> 快捷菜单（预览页面 / 设置 / 退出；查找与打开应用走输入条指令）
- 双击角色   -> 开心跳跃（jump 动画 + happy 表情，角色本体反应）
- 空闲两分钟 -> 主动搭话（可在设置中关闭）
"""

from PyQt5.QtCore import Qt, QRect, QTimer, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QWidget, QApplication, QMenu, QAction
import time

from framework import config
from framework.core import Framework
from ui.input_dialog import InputDialog, HistoryPanel
from ui import theme
from ui.icons import icon as stroke_icon
from ui.sprite_manager import SpriteManager
from ui.sound_manager import SoundManager
from ui.preview_window import PreviewWindow
from ui.ui_manager import BubbleWidget, InfoCardWidget, SettingsDialog, TrayIcon
from framework.utils import logger


def _flash_label(label, message, restore="", ms=2600):
    """面板按钮点完给一句看得见的结果，几秒后自动清空。

    用面板里独立的反馈标签，而不是复用统计标签：提醒/待办面板有 1~2 秒的
    定时刷新，会把统计文案每秒重写一次，反馈会被立刻冲掉。
    """
    label.setText(message)

    def _restore():
        try:
            label.setText(restore() if callable(restore) else restore)
        except RuntimeError:
            pass    # 面板已关闭，标签控件已销毁
    QTimer.singleShot(ms, _restore)


def _icon_button(text: str, name: str, color_key: str = "TEXT2",
                 size: int = 15):
    """面板按钮统一用线框图标，不再往按钮文字里塞彩色 emoji。"""
    from PyQt5.QtWidgets import QPushButton
    btn = QPushButton(text)
    btn.setIcon(stroke_icon(name, theme.T(color_key), size))
    return btn


def _make_status(layout):
    """在按钮行上方插一条专门显示操作结果的标签。"""
    from PyQt5.QtWidgets import QLabel
    label = QLabel("")
    label.setWordWrap(True)
    layout.addWidget(label)
    return label


def _confirm_action(parent, title, message):
    """清空 / 删除类不可逆操作的统一确认框。"""
    from PyQt5.QtWidgets import QMessageBox
    return QMessageBox.question(
        parent, title, message, QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No) == QMessageBox.Yes


class PetWindow(QWidget):
    """桌面角色主窗口"""

    # 供调试/自测使用的信号
    command_sent = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        # ---- Framework 单例 + 六个关键操作之一：注册回调 ----
        self.fw = Framework.instance()

        # ---- 窗口属性：无边框 / 透明 / 置顶 / 工具窗口 ----
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(config.PET_SIZE, config.PET_SIZE)

        # ---- 动画管理 ----
        self.sprite = SpriteManager(self)
        self.sprite.set_pose("idle")
        self._direction = 1          # 当前朝向: 1右 -1左

        # ---- 动画帧定时器 ----
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._restart_anim_timer()

        # ---- 拖拽状态 ----
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._press_pos = None
        self._moved_during_press = False
        self._plan_card_was_visible = False   # 截图前计划卡是否可见

        # ---- 音效（语音包 400 Sounds Pack）----
        self.sound = SoundManager()

        # ---- UI 组件（气泡/卡片带音效）----
        self.bubble = BubbleWidget(sound=self.sound)
        self.card = InfoCardWidget(sound=self.sound)
        self.input_box = InputDialog()
        self.settings = SettingsDialog()
        self.tray = TrayIcon()
        self.preview = None                  # 预览页面（懒加载）
        self.workspace = None                # 可最大化的主工作台（懒加载）
        self._workspace_was_visible = False  # 截图前工作台是否可见
        self._workspace_mode = False         # 工作台打开中：桌宠本体与浮层整体消失

        # ---- 注册 Framework 回调 ----
        self.fw.register_callback("show_bubble", self._on_bubble)
        self.fw.register_callback("show_card", self._on_card)
        self.fw.register_callback("set_pose", self.on_set_pose)
        self.fw.register_callback("set_emotion", self.sprite.set_emotion)

        # ---- Framework 宠物控制器 -> 窗口位置同步 ----
        self.fw.pet.position_changed.connect(self._on_pet_moved)
        self.fw.pet.move_finished.connect(self._on_move_finished)

        # ---- 输入框提交 ----
        self.input_box.command_submitted.connect(self._on_command)
        # ---- 聊天记录面板（独立窗口）：绑定记忆系统，随对话更新 ----
        self.history_panel = HistoryPanel()
        self.history_panel.bind_memory(self.fw.memory)
        self.fw.history_changed.connect(self.history_panel.refresh_history)
        self.history_panel.resend.connect(self._on_resend)
        self.input_box.history_requested.connect(self._show_history)

        # ---- 截图联动：抓屏前隐藏所有窗口，抓完恢复 ----
        self.fw.screenshot_hide.connect(self._on_screenshot_hide)
        self.fw.screenshot_restore.connect(self._on_screenshot_restore)

        # ---- 设置窗口信号 ----
        self.settings.personality_changed.connect(self.fw.set_personality)
        self.settings.autostart_changed.connect(self._set_autostart)
        self.settings.proactive_changed.connect(self._set_proactive)
        self.settings.doc_changed.connect(self._set_doc_enabled)
        self.settings.pexels_key_changed.connect(self._set_pexels_key)
        self.settings.vision_config_changed.connect(self._set_vision_config)
        self.settings.websearch_key_changed.connect(self._set_websearch_key)
        self.settings.sound_changed.connect(self.sound.set_enabled)
        self.settings.volume_changed.connect(self.sound.set_volume)
        self.settings.ai_config_changed.connect(self._apply_ai_config)
        self.settings.test_ai_requested.connect(self._test_ai_connection)
        self.settings.lora_config_changed.connect(self._apply_lora_config)
        self.settings.test_lora_requested.connect(self._test_lora)
        self.settings.exit_requested.connect(self.quit_app)
        self.settings.refresh_status(self.fw.ai_status_text())
        self.tray.exit_requested.connect(self.quit_app)
        self.tray.settings_requested.connect(self.show_settings)
        self.tray.preview_requested.connect(self.show_preview)
        self.tray.workspace_requested.connect(self.open_workspace)
        self.input_box.workspace_requested.connect(self.open_workspace)

        # ---- 右键菜单 ----
        self._menu = self._build_menu()

        # ---- 拖拽投喂文件（pptx/docx 拖到凌零身上进入修改流程）----
        self.setAcceptDrops(True)

        # ---- 初始位置：屏幕右下角 ----
        x, y = self.fw.get_anchor("bottom_right")
        self.move(x, y)
        self.fw.pet.set_position(x, y)

        # ---- 窗口图标（Alt-Tab / 预览页 / 设置窗都能看到）----
        self._apply_window_icon()

        # ---- 空闲计时（主动互动）----
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(1000)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_seconds = 0
        self._proactive_enabled = config.PROACTIVE_ENABLED
        self._idle_timer.start()

        # ---- 待机散步 ----
        self.fw.pet.start_wander()

        self.tray.show()
        # 启动提示：告诉用户去哪儿找（Win11 托盘常被折叠）
        self.tray.showMessage(
            "凌零在这里~",
            "我就在右下角任务栏托盘里，右键我可以设置和退出",
            TrayIcon.Information, 4000,
        )
        logger.info("[PetWindow] 初始化完成")

    def _apply_window_icon(self):
        """给窗口设置软件图标（assets/icon.ico）"""
        import os
        from PyQt5.QtGui import QIcon
        from framework.utils import resource_path
        path = resource_path(os.path.join("assets", "icon.ico"))
        if os.path.exists(path):
            icon = QIcon(path)
            self.setWindowIcon(icon)
            QApplication.instance().setWindowIcon(icon)

    # ============================================================
    # 动画渲染
    # ============================================================
    def _restart_anim_timer(self):
        """按当前状态帧率重启动画定时器"""
        self._anim_timer.start(self.sprite.frame_interval_ms())

    def _on_anim_tick(self):
        """推进一帧并重绘（姿态变化会自动同步帧率）"""
        self.sprite.advance()
        if self._anim_timer.interval() != self.sprite.frame_interval_ms():
            self._restart_anim_timer()
        self.update()

    def on_set_pose(self, pose: str):
        """Framework 姿态回调：切换姿态并按新帧率重启定时器"""
        self.sprite.set_pose(pose)
        if pose == "jump":
            self.sound.play("whoosh")
        elif pose == "attack":
            # 单次表演：挥完一轮剑自动回 idle（不覆盖期间的新姿态）
            QTimer.singleShot(self.sprite.pose_duration_ms() + 60,
                              self._finish_attack)
        self._restart_anim_timer()

    def _finish_attack(self):
        if self.sprite.pose == "attack":
            self.fw.set_pose("idle")

    def paintEvent(self, event):
        """绘制当前帧 + 粒子/情绪实时层（情绪会带动身体表演位移）"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dx, dy = self.sprite.performance_offset()
        s = self.width() / 128.0
        painter.drawPixmap(int(dx * s), int(dy * s),
                           self.sprite.current_pixmap())
        # 粒子特效 + 情绪表情（实时层，不随身体位移）
        self.sprite.draw_overlay(painter, self.width())

    # ============================================================
    # 鼠标交互：单击弹输入框 / 拖拽移动 / 双击开心跳 / 右键菜单
    # ============================================================
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._moved_during_press = False
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPos()
            self._idle_seconds = 0
        elif event.button() == Qt.RightButton:
            self._menu.exec_(event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            new_pos = event.globalPos() - self._drag_offset
            if self._press_pos and (new_pos - self._press_pos).manhattanLength() > 6:
                self._moved_during_press = True
            self.move(new_pos)
            self.fw.pet.set_position(new_pos.x(), new_pos.y())
            self._sync_followers()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            # 未拖动的单击 -> 弹出输入框
            if not self._moved_during_press:
                self._show_input()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """双击角色：开心跳跃一下（与 AI 的 jump 动作同一套联动：
        jump 姿态 + whoosh 音效，跳完自动回 idle）"""
        if event.button() == Qt.LeftButton:
            self.fw.set_emotion("happy")
            self.fw.set_pose("jump")
            self.fw.pet.jump(on_done=lambda: self.fw.set_pose("idle"))
        super().mouseDoubleClickEvent(event)

    # ============================================================
    # 输入框 / 指令
    # ============================================================
    def _show_input(self):
        """单击角色：弹出跟随输入框"""
        self.input_box.popup_at(self.x(), self.y())
        self._idle_seconds = 0

    def _show_history(self):
        """输入条上的「记录」按钮：在角色侧面打开聊天记录面板"""
        self.history_panel.popup_at(self.x(), self.y())

    def _on_resend(self, text: str):
        """聊天记录双击某条：填回输入条并弹出"""
        self.input_box.popup_at(self.x(), self.y())
        self.input_box.set_text(text)

    def _on_command(self, text: str):
        """用户提交指令 -> Framework"""
        self.sound.play("click")
        self.command_sent.emit(text)
        self.fw.send_command(text)

    # ============================================================
    # Framework 回调与位置同步
    # ============================================================
    def _on_bubble(self, text: str):
        """显示气泡（锚定角色头顶）"""
        if self._workspace_mode:
            return   # 工作台打开中：桌宠浮层整体消失，不弹气泡
        self.bubble.show_text(text, self.x(), self.y())

    def _on_card(self, data: dict):
        """显示信息卡片（出现在角色旁边）"""
        if self._workspace_mode:
            return   # 工作台打开中：不弹信息卡片
        self.card.show_card(
            data.get("title", ""), data.get("path", ""), data.get("size", ""),
            near_x=self.x(), near_y=self.y(), icon=data.get("icon", ""),
        )

    def _on_pet_moved(self, x: int, y: int, direction: int):
        """宠物控制器移动 -> 更新窗口位置与朝向"""
        if direction != 0:
            self._direction = direction
            self.sprite.set_direction(direction)
        self.move(x, y)
        self._sync_followers()

    def _on_move_finished(self, tag: str):
        """一段移动完成：占位（如需在移动结束时切换姿态可在此扩展）"""
        pass

    def _sync_followers(self):
        """气泡/输入框/记录面板/计划卡跟随角色"""
        self.bubble.follow(self.x(), self.y())
        self.input_box.follow(self.x(), self.y())
        self.history_panel.follow(self.x(), self.y())
        plan_card = getattr(self.fw, "plan_card", None)
        if plan_card is not None:
            plan_card.follow(self.x(), self.y())

    # ============================================================
    # 截图联动
    # ============================================================
    def _on_screenshot_hide(self):
        """截图瞬间隐藏所有助手相关窗口（不入镜）"""
        self.hide()
        self.bubble.hide()
        self.input_box.hide()
        self.history_panel.hide()
        plan_card = getattr(self.fw, "plan_card", None)
        self._plan_card_was_visible = bool(plan_card and plan_card.isVisible())
        if plan_card is not None:
            plan_card.hide()
        # 工作台也是助手窗口，截图时不该入镜
        self._workspace_was_visible = bool(
            self.workspace is not None and self.workspace.isVisible())
        if self._workspace_was_visible:
            self.workspace.hide()

    def _on_screenshot_restore(self):
        """截图完成，恢复角色显示"""
        if not self.isVisible():
            self.show()
        plan_card = getattr(self.fw, "plan_card", None)
        if plan_card is not None and self._plan_card_was_visible:
            plan_card.show()
        if self._workspace_was_visible and self.workspace is not None:
            self.workspace.show()

    # ============================================================
    # 右键快捷菜单
    # ============================================================
    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        act_workspace = QAction("打开工作台（可最大化）", menu)
        act_workspace.triggered.connect(self.open_workspace)
        act_preview = QAction("预览页面", menu)
        act_preview.triggered.connect(self.show_preview)
        act_edit_doc = QAction("修改文件（PPT/Word）", menu)
        act_edit_doc.triggered.connect(self._pick_doc_to_edit)
        act_workflows = QAction("工作流模板", menu)
        act_workflows.triggered.connect(self._show_workflows)
        act_memory = QAction("我的记忆", menu)
        act_memory.triggered.connect(self._show_memory)
        act_reminders = QAction("我的提醒", menu)
        act_reminders.triggered.connect(self._show_reminders)
        act_todos = QAction("我的待办", menu)
        act_todos.triggered.connect(self._show_todos)
        act_deliveries = QAction("我的交付", menu)
        act_deliveries.triggered.connect(self._show_deliveries)
        act_worklog = QAction("工作日志", menu)
        act_worklog.triggered.connect(self._show_worklog)
        act_undo = QAction("撤销上一轮", menu)
        act_undo.triggered.connect(lambda: self.fw.send_command("撤销"))
        act_skills = QAction("我的技能", menu)
        act_skills.triggered.connect(lambda: self.fw.send_command("技能列表"))
        act_save_skill = QAction("把上一轮存成技能", menu)
        act_save_skill.triggered.connect(
            lambda: self.fw.send_command("存成技能"))
        act_settings = QAction("设置", menu)
        act_settings.triggered.connect(self.show_settings)
        act_exit = QAction("退出", menu)
        act_exit.triggered.connect(self.quit_app)
        for a in (act_workspace, act_preview, act_edit_doc, act_workflows, act_undo,
                  act_save_skill, act_skills, act_memory, act_reminders,
                  act_todos, act_deliveries, act_worklog, act_settings,
                  act_exit):
            menu.addAction(a)
        return menu

    def _pick_doc_to_edit(self):
        """右键菜单：选一个 pptx/docx 交给凌零修改"""
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要修改的文件", "",
            "文档 (*.pptx *.docx);;PPT (*.pptx);;Word (*.docx);;"
            "所有文件 (*)")
        if path:
            self.fw.request_doc_edit(path)

    def _show_workflows(self):
        """右键菜单：一键工作流模板（选中运行，凌零按模板自动编排多步任务）"""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                     QListWidgetItem, QHBoxLayout,
                                     QPushButton, QLabel)

        dlg = QDialog(self)
        dlg.setWindowTitle("凌零的工作流模板")
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        hint = ("一键工作流：选中后点「运行选中」（或双击），"
                "凌零会先拟计划给你确认，再自动逐步执行：")
        tip = QLabel(hint)
        tip.setWordWrap(True)
        layout.addWidget(tip)
        listw = QListWidget()
        listw.itemDoubleClicked.connect(lambda _item: do_run())
        layout.addWidget(listw, 1)

        def _refresh():
            listw.clear()
            for t in config.WORKFLOW_TEMPLATES:
                item = QListWidgetItem(f"{t['name']}　—　{t['desc']}")
                item.setIcon(stroke_icon("play", theme.T("ACCENT"), 16))
                item.setData(Qt.UserRole, t["name"])
                listw.addItem(item)

        _refresh()
        status = _make_status(layout)
        btns = QHBoxLayout()
        run_btn = _icon_button("运行选中", "play", "ACCENT_TEXT")
        close_btn = QPushButton("关闭")
        btns.addWidget(run_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        def do_run():
            row = listw.currentRow()
            if row < 0:
                _flash_label(status, "注意：请先选中一个模板，再点「运行选中」")
                return
            name = listw.item(row).data(Qt.UserRole)
            tpl = next((t for t in config.WORKFLOW_TEMPLATES
                        if t["name"] == name), None)
            if tpl:
                dlg.close()
                self.sound.play("click")
                self.fw.send_command(tpl["command"])
            else:
                _flash_label(status, "注意：找不到这个模板，请重新选择")

        run_btn.clicked.connect(do_run)
        close_btn.clicked.connect(dlg.close)
        dlg.resize(780, 420)
        dlg.show()

    def _show_memory(self):
        """右键菜单：查看/管理长期记忆（AI 通过 remember 动作写入的内容）"""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                     QHBoxLayout, QPushButton, QLabel)
        dlg = QDialog(self)
        dlg.setWindowTitle("凌零的长期记忆")
        layout = QVBoxLayout(dlg)

        def _tip_text():
            return (f"共 {len(self.fw.memory.facts)} 条"
                    f"（凌零长期记着，重启不丢）：")

        tip = QLabel(_tip_text())
        layout.addWidget(tip)
        listw = QListWidget()
        for fact in self.fw.memory.facts:
            listw.addItem(fact)
        layout.addWidget(listw)
        status = _make_status(layout)
        btns = QHBoxLayout()
        del_btn = QPushButton("删除选中")
        clear_btn = QPushButton("全部清空")
        close_btn = QPushButton("关闭")
        btns.addWidget(del_btn)
        btns.addWidget(clear_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        def do_del():
            row = listw.currentRow()
            if row < 0:
                _flash_label(status, "注意：请先选中一条记忆")
                return
            if self.fw.memory.forget_fact(row) is not None:
                listw.takeItem(row)
                tip.setText(_tip_text())
                _flash_label(status, "✓ 已删除这条记忆")
            else:
                _flash_label(status, "! 删除失败：这条记忆已不存在")

        def do_clear():
            n = len(self.fw.memory.facts)
            if not n:
                _flash_label(status, "本来就没有记忆可清空")
                return
            if not _confirm_action(
                    dlg, "清空长期记忆",
                    "确定删除全部 %d 条长期记忆吗？此操作不可撤销。" % n):
                _flash_label(status, "已取消，记忆未改动")
                return
            self.fw.memory.facts.clear()
            self.fw.memory.save()
            listw.clear()
            tip.setText(_tip_text())
            _flash_label(status, "✓ 已清空 %d 条记忆" % n)

        del_btn.clicked.connect(do_del)
        clear_btn.clicked.connect(do_clear)
        close_btn.clicked.connect(dlg.close)
        dlg.resize(380, 420)
        dlg.show()

    def _show_reminders(self):
        """右键菜单：查看/管理定时提醒（显示每条还剩多久，到点自动删除）"""
        import time as _time
        from PyQt5.QtCore import QTimer as _QTimer
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                     QHBoxLayout, QPushButton, QLabel)
        from framework.task_board import format_duration

        dlg = QDialog(self)
        dlg.setWindowTitle("凌零的提醒")
        layout = QVBoxLayout(dlg)

        def _tip_text():
            n = len(self.fw.tasks.reminders)
            return (f"共 {n} 条提醒（到点自动删除，凌零会连跳提醒你）："
                    if n else "暂无提醒。对我说「提醒我 30 分钟后……」即可添加：")

        tip = QLabel(_tip_text())
        layout.addWidget(tip)
        listw = QListWidget()
        layout.addWidget(listw)

        def _refresh():
            now = _time.time()
            row = listw.currentRow()
            listw.clear()
            for r in self.fw.tasks.reminders:
                remain = r["at"] - now
                listw.addItem(f"{r['text']}   （还剩 "
                              f"{format_duration(remain)}）")
            if 0 <= row < listw.count():
                listw.setCurrentRow(row)
            tip.setText(_tip_text())

        refresh_timer = _QTimer(dlg)
        refresh_timer.timeout.connect(_refresh)
        refresh_timer.start(1000)
        _refresh()

        status = _make_status(layout)
        btns = QHBoxLayout()
        del_btn = QPushButton("删除选中")
        close_btn = QPushButton("关闭")
        btns.addWidget(del_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        def do_del():
            row = listw.currentRow()
            if row < 0:
                _flash_label(status, "注意：请先选中一条提醒")
                return
            if self.fw.tasks.remove_reminder(row + 1) is not None:
                _refresh()
                _flash_label(status, "✓ 已删除这条提醒")
            else:
                _flash_label(status, "! 删除失败：这条提醒已不存在")

        del_btn.clicked.connect(do_del)
        close_btn.clicked.connect(dlg.close)
        dlg.resize(400, 360)
        dlg.show()

    def _show_todos(self):
        """右键菜单：查看/管理待办清单（大面板，一眼看清）"""
        from PyQt5.QtCore import QTimer as _QTimer
        from PyQt5.QtGui import QBrush, QColor
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                     QListWidgetItem, QHBoxLayout,
                                     QPushButton, QLabel)

        dlg = QDialog(self)
        dlg.setWindowTitle("凌零的待办")
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        def _tip_text():
            n = len(self.fw.tasks.open_todos())
            done = len(self.fw.tasks.todos) - n
            if n:
                return (f"待办 {n} 项，已完成 {done} 项。"
                        "对我说「加个待办：……」即可添加：")
            return "暂无待办。对我说「加个待办：……」即可添加："

        tip = QLabel(_tip_text())
        layout.addWidget(tip)
        listw = QListWidget()
        layout.addWidget(listw, 1)

        def _refresh():
            row = listw.currentRow()
            listw.clear()
            open_n = 0
            for i, t in enumerate(self.fw.tasks.todos):
                item = QListWidgetItem()
                if t["done"]:
                    item.setText(f"{t['text']}　（已完成）")
                    item.setIcon(stroke_icon(
                        "check-circle", theme.T("GOOD"), 16))
                    item.setForeground(QBrush(QColor(160, 150, 135)))
                    item.setData(Qt.UserRole, (i + 1, None))
                else:
                    open_n += 1
                    item.setText(f"◻ {t['text']}")
                    item.setData(Qt.UserRole, (i + 1, open_n))
                listw.addItem(item)
            if not self.fw.tasks.todos:
                ph = QListWidgetItem("（空空如也，让凌零帮你记点事吧）")
                ph.setFlags(Qt.NoItemFlags)
                ph.setForeground(QBrush(QColor(170, 158, 142)))
                listw.addItem(ph)
            if 0 <= row < listw.count():
                listw.setCurrentRow(row)
            tip.setText(_tip_text())

        # 凌零可能随时往里加待办，定时刷新保持最新
        refresh_timer = _QTimer(dlg)
        refresh_timer.timeout.connect(_refresh)
        refresh_timer.start(2000)
        _refresh()

        status = _make_status(layout)
        btns = QHBoxLayout()
        done_btn = _icon_button("完成选中", "check", "GOOD")
        del_btn = _icon_button("删除选中", "trash")
        clear_btn = _icon_button("清理已完成", "x-circle")
        close_btn = QPushButton("关闭")
        btns.addWidget(done_btn)
        btns.addWidget(del_btn)
        btns.addWidget(clear_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        def do_done():
            row = listw.currentRow()
            if row < 0:
                _flash_label(status, "注意：请先选中一条待办")
                return
            data = listw.item(row).data(Qt.UserRole)
            if not data or not data[1]:
                _flash_label(status, "这条已经是完成状态了")
                return
            if self.fw.tasks.complete_todo(data[1]) is not None:
                _refresh()
                _flash_label(status, "✓ 已标记完成")
            else:
                _flash_label(status, "! 操作失败：待办已变化，请重试")

        def do_del():
            row = listw.currentRow()
            if row < 0:
                _flash_label(status, "注意：请先选中一条待办")
                return
            data = listw.item(row).data(Qt.UserRole)
            if data and self.fw.tasks.remove_todo(data[0]) is not None:
                _refresh()
                _flash_label(status, "✓ 已删除这条待办")
            else:
                _flash_label(status, "! 删除失败：待办已变化，请重试")

        def do_clear():
            done = len(self.fw.tasks.todos) - len(self.fw.tasks.open_todos())
            if not done:
                _flash_label(status, "没有已完成的待办可清理")
                return
            self.fw.tasks.clear_done()
            _refresh()
            _flash_label(status, "✓ 已清理 %d 条已完成待办" % done)

        done_btn.clicked.connect(do_done)
        del_btn.clicked.connect(do_del)
        clear_btn.clicked.connect(do_clear)
        close_btn.clicked.connect(dlg.close)
        dlg.resize(780, 660)
        dlg.show()

    def _show_deliveries(self):
        """右键菜单：查看/打开凌零交付过的文件（可验收的工作成果）"""
        import os as _os
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                     QListWidgetItem, QHBoxLayout,
                                     QPushButton, QLabel)

        dlg = QDialog(self)
        dlg.setWindowTitle("凌零的交付")
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        kind_label = {"ppt": "PPT", "doc": "Word", "xlsx": "Excel",
                      "html": "网页"}

        def _tip_text():
            n = len(self.fw.tasks.deliveries)
            return (f"凌零交付过 {n} 份文件（双击即可打开）："
                    if n else "还没有交付记录。让凌零做个 PPT / 报告试试：")

        tip = QLabel(_tip_text())
        layout.addWidget(tip)
        listw = QListWidget()
        listw.itemDoubleClicked.connect(
            lambda item: self._open_path(item.data(Qt.UserRole), status))
        layout.addWidget(listw, 1)

        def _refresh():
            listw.clear()
            for d in self.fw.tasks.deliveries:
                lt = time.localtime(d.get("ts", 0))
                item = QListWidgetItem(
                    f"{d['name']}　（{kind_label.get(d.get('kind'), '文件')}"
                    f"，{time.strftime('%m-%d %H:%M', lt)}）")
                item.setIcon(stroke_icon("file-text", theme.T("TEXT2"), 16))
                item.setData(Qt.UserRole, d.get("path"))
                listw.addItem(item)
            if not self.fw.tasks.deliveries:
                ph = QListWidgetItem("（空空如也，吩咐凌零做份文档吧）")
                ph.setFlags(Qt.NoItemFlags)
                listw.addItem(ph)
            tip.setText(_tip_text())

        _refresh()
        status = _make_status(layout)
        btns = QHBoxLayout()
        open_btn = _icon_button("打开选中", "folder")
        del_btn = _icon_button("删除记录", "trash")
        close_btn = QPushButton("关闭")
        btns.addWidget(open_btn)
        btns.addWidget(del_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        def _selected_path():
            row = listw.currentRow()
            return listw.item(row).data(Qt.UserRole) if row >= 0 else None

        def do_open():
            self._open_path(_selected_path(), status)

        def do_del():
            row = listw.currentRow()
            if row < 0:
                _flash_label(status, "注意：请先选中一条交付记录")
                return
            if self.fw.tasks.remove_delivery(row + 1) is not None:
                _refresh()
                _flash_label(status, "✓ 已删除这条交付记录")
            else:
                _flash_label(status, "! 删除失败：记录已变化，请重试")

        open_btn.clicked.connect(do_open)
        del_btn.clicked.connect(do_del)
        close_btn.clicked.connect(dlg.close)
        dlg.resize(780, 620)
        dlg.show()

    def _open_path(self, path, label=None):
        """用系统默认程序打开交付文件；成功、没选中、文件丢失都有反馈。"""
        import os as _os

        def _say(msg):
            if label is not None:
                _flash_label(label, msg)
            else:
                self.tray.showMessage("凌零 · 交付", msg,
                                      TrayIcon.Information, 3000)

        if not path:
            _say("注意：请先选中一份交付记录")
            return False
        if not _os.path.exists(path):
            _say("注意：文件已不在原位置：%s" % path)
            return False
        try:
            _os.startfile(path)     # noqa: S606 Windows 专用
        except OSError as e:
            logger.warning(f"[Deliveries] 打开失败: {e}")
            _say("注意：打开失败：%s" % e)
            return False
        _say("✓ 已在默认程序打开：%s" % _os.path.basename(str(path)))
        return True

    def _show_worklog(self):
        """右键菜单：凌零的工作日志（数字员工做过的事，按时间流水）"""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                     QListWidgetItem, QHBoxLayout,
                                     QPushButton, QLabel)

        dlg = QDialog(self)
        dlg.setWindowTitle("凌零的工作日志")
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        def _tip_text():
            n = len(self.fw.tasks.worklog)
            return (f"凌零替你做过 {n} 件事（重启不丢）："
                    if n else "凌零还没干过活。吩咐它做事，这里会记账：")

        tip = QLabel(_tip_text())
        layout.addWidget(tip)
        listw = QListWidget()
        layout.addWidget(listw, 1)

        def _refresh():
            listw.clear()
            for w in self.fw.tasks.worklog:
                lt = time.localtime(w.get("ts", 0))
                listw.addItem(f"{time.strftime('%m-%d %H:%M', lt)}  "
                              f"{w['text']}")
            if not self.fw.tasks.worklog:
                ph = QListWidgetItem("（还没有工作记录）")
                ph.setFlags(Qt.NoItemFlags)
                listw.addItem(ph)
            tip.setText(_tip_text())

        _refresh()
        status = _make_status(layout)
        btns = QHBoxLayout()
        clear_btn = _icon_button("清空日志", "x-circle")
        close_btn = QPushButton("关闭")
        btns.addWidget(clear_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        def do_clear():
            n = len(self.fw.tasks.worklog)
            if not n:
                _flash_label(status, "工作日志本来就是空的")
                return
            if not _confirm_action(
                    dlg, "清空工作日志",
                    "确定清空全部 %d 条工作记录吗？此操作不可撤销。" % n):
                _flash_label(status, "已取消，日志未改动")
                return
            self.fw.tasks.worklog = []
            self.fw.tasks.save()
            _refresh()
            _flash_label(status, "✓ 已清空 %d 条工作记录" % n)

        clear_btn.clicked.connect(do_clear)
        close_btn.clicked.connect(dlg.close)
        dlg.resize(780, 620)
        dlg.show()

    # ============================================================
    # 拖拽投喂：把 pptx/docx 拖到凌零身上即可进入文件修改
    # ============================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith((".pptx", ".docx")):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".pptx", ".docx")):
                event.acceptProposedAction()
                self.fw.request_doc_edit(path)
                return
        event.ignore()

    def open_workspace(self):
        """点开可最大化的主工作台（右键 / 托盘 / 输入条三处入口共用）。

        懒加载：第一次才构造窗口；关闭只隐藏不销毁，下次点开立刻回来。
        首次打开默认按记住的偏好最大化，用户切成窗口模式后会被记住。
        """
        if self.workspace is None:
            from ui.workspace_window import WorkspaceWindow
            self.workspace = WorkspaceWindow(self.fw)
            self.workspace.settings_requested.connect(self.show_settings)
            # 「文件 → 退出程序」要真退出，不能只是发一个没人接的信号
            self.workspace.quit_requested.connect(self.quit_app)
            # 工作台关掉/回桌宠时，把桌宠本体重新放出来
            self.workspace.workspace_closed.connect(self._on_workspace_closed)
        self.workspace.open_workspace()
        # 工作台铺开后收起桌宠本体与浮层，避免两者叠在桌面上
        self._hide_pet_for_workspace()

    def _hide_pet_for_workspace(self):
        """打开工作台时让桌宠整体消失：本体 + 所有浮层一并收起。

        光 hide 本体不够——气泡、信息卡、输入条是独立置顶窗口，工作台里
        继续对话时 _on_bubble/_on_card 还会把它们重新弹出来。所以这里
        同时挂起「工作台模式」，在此期间这些回调直接短路，浮层不再冒出。
        """
        self._workspace_mode = True
        self.hide()
        self.bubble.hide()
        self.card.hide()
        self.input_box.hide()
        self.history_panel.hide()
        plan_card = getattr(self.fw, "plan_card", None)
        if plan_card is not None:
            plan_card.hide()

    def _on_workspace_closed(self):
        """工作台关闭（点「回桌宠」/按 Esc / 点 X）后，重新显示桌宠本体。"""
        self._workspace_mode = False
        self.show()
        self.raise_()

    def show_preview(self):
        """打开助手预览页面（前端组件调试台）"""
        if self.preview is None:
            self.preview = PreviewWindow()
        self.preview.show()
        self.preview.raise_()
        self.preview.activateWindow()

    def show_settings(self):
        """显示设置窗口（贴着角色弹出，越界时自动收进屏幕）"""
        self.settings.refresh_status(self.fw.ai_status_text())
        self.settings.refresh_lora_status(self.fw.lora_status_text())
        self.settings.show_near(QRect(self.x(), self.y(),
                                      self.width(), self.height()))

    # ============================================================
    # 设置项
    # ============================================================
    def _set_autostart(self, enabled: bool):
        """开机自启：写注册表 Run 键"""
        import os
        import sys
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            with key:
                if enabled:
                    exe = sys.executable
                    target = (f'"{exe}" "{os.path.abspath("main.py")}"'
                              if not getattr(sys, "frozen", False)
                              else f'"{exe}"')
                    winreg.SetValueEx(key, "DesktopAssistant", 0, winreg.REG_SZ, target)
                else:
                    try:
                        winreg.DeleteValue(key, "DesktopAssistant")
                    except FileNotFoundError:
                        pass
            config.AUTOSTART_ENABLED = enabled
            logger.info(f"[Settings] 开机自启: {enabled}")
        except OSError as e:
            logger.warning(f"[Settings] 开机自启设置失败: {e}")

    def _apply_ai_config(self, api_key: str, base_url: str, model: str):
        """用户在设置界面保存的 DeepSeek 配置（自动切换真实AI并持久化）"""
        self.fw.set_ai_config(api_key, base_url, model)
        self.settings.set_ai_status(self.fw.ai_status_text())
        self.sound.play("click")

    def _test_ai_connection(self, api_key: str, base_url: str, model: str):
        """后台线程测试连接，避免界面卡住"""
        from PyQt5.QtCore import QThread, QObject, pyqtSignal

        class _Tester(QObject):
            done = pyqtSignal(bool, str)

            def run(self, k, u, m):
                from framework.ai_engine import test_connection
                ok, msg = test_connection(k, u, m)
                self.done.emit(ok, msg)

        self._tester = _Tester()
        thread = QThread(self)
        self._tester.moveToThread(thread)
        thread.started.connect(
            lambda: self._tester.run(api_key, base_url, model)
        )
        self._tester.done.connect(
            lambda ok, msg: (self.settings.set_ai_status(
                ("✓ " if ok else "✗ ") + msg), thread.quit())
        )
        self._tester.done.connect(self._tester.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _apply_lora_config(self, enabled: bool, base_model: str,
                            adapter_path: str, device: str):
        """用户在设置界面保存的本地微调模型配置（自动持久化）"""
        self.fw.set_lora_config(enabled, base_model, adapter_path, device)
        self.settings.set_lora_status(self.fw.lora_status_text())
        self.sound.play("click")

    def _test_lora(self, base_model: str, adapter_path: str, device: str):
        """后台线程测试本地微调模型能不能加载（加载很慢，别卡住界面）"""
        from PyQt5.QtCore import QThread, QObject, pyqtSignal, Qt

        class _Tester(QObject):
            done = pyqtSignal(bool, str)

            def run(self, b, a, d):
                from framework.lora_engine import test_load
                ok, msg = test_load(b, a, d)
                self.done.emit(ok, msg)

        self._lora_tester = _Tester()
        thread = QThread(self)
        self._lora_thread = thread
        self._lora_tester.moveToThread(thread)
        thread.started.connect(
            lambda: self._lora_tester.run(base_model, adapter_path, device)
        )
        # 跨线程回 GUI：显式排队，别在工作线程里直接碰控件
        self._lora_tester.done.connect(self._on_lora_test_done,
                                       Qt.QueuedConnection)
        self._lora_tester.done.connect(self._lora_tester.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_lora_test_done(self, ok: bool, msg: str):
        """本地微调加载测试的结果（回到 GUI 线程才更新界面）"""
        self.settings.set_lora_status(("✓ " if ok else "✗ ") + msg)
        thread = getattr(self, "_lora_thread", None)
        if thread is not None:
            thread.quit()

    def _set_proactive(self, enabled: bool):
        """主动互动开关"""
        self._proactive_enabled = enabled
        config.PROACTIVE_ENABLED = enabled
        logger.info(f"[Settings] 主动互动: {enabled}")

    def _set_doc_enabled(self, enabled: bool):
        """文档生成总开关（持久化到用户设置）"""
        config.ALLOW_DOC_GENERATION = enabled
        config.save_user_settings(doc_enabled=enabled)
        logger.info(f"[Settings] 允许创建文件: {enabled}")

    def _set_pexels_key(self, key: str):
        """图库 Key（联网配图用，持久化到用户设置）"""
        config.PEXELS_API_KEY = key
        config.save_user_settings(pexels_key=key)
        logger.info(f"[Settings] 图库Key: {'已设置' if key else '已清除'}")

    def _set_vision_config(self, key: str, url: str, model: str):
        """视觉模型配置（屏幕理解用，持久化到用户设置）"""
        config.VISION_API_KEY = key
        config.VISION_API_URL = url
        config.VISION_MODEL = model
        config.save_user_settings(vision_key=key, vision_url=url,
                                  vision_model=model)
        logger.info(f"[Settings] 视觉Key: {'已设置' if key else '已清除'}，"
                    f"视觉模型: {model}")

    def _set_websearch_key(self, key: str):
        """搜索 Key（联网调研用，持久化到用户设置）"""
        config.WEBSEARCH_API_KEY = key
        config.save_user_settings(websearch_key=key)
        logger.info(f"[Settings] 搜索Key: {'已设置' if key else '已清除'}")

    # ============================================================
    # 空闲检测（主动互动）
    # ============================================================
    def _check_idle(self):
        """两分钟无操作 -> 角色走到屏幕中间主动搭话"""
        self._idle_seconds += 1
        if (self._proactive_enabled
                and self._idle_seconds >= config.IDLE_TIMEOUT_MS // 1000
                and not self.fw.pet.is_moving):
            self._idle_seconds = 0

            def _arrive():
                self.fw.proactive_chat()

            self.fw.pet.go_center(on_done=_arrive)

    # ============================================================
    # 退出
    # ============================================================
    def quit_app(self):
        logger.info("[PetWindow] 退出")
        if self.workspace is not None:
            try:
                self.workspace.save_state()
                self.workspace.detach()
            except Exception as e:      # noqa: BLE001 收尾不能挡住退出
                logger.warning(f"[PetWindow] 工作台收尾失败: {e}")
        self.fw.shutdown()
        self.tray.hide()
        QApplication.quit()

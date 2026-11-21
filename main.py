# main.py
import sys
import os
import json
import socket
import time
import requests
from datetime import datetime
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QLabel,
    QStatusBar,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QMenu,
    QTabWidget,
    QDialog,
)

import shutil
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt, QUrl, QSize, QEvent

from app_settings import load_settings, save_settings, SettingsDialog
from PyQt5.QtGui import (
    QColor,
    QFont,
    QDesktopServices,
    QPixmap,
    QIcon,
    QMovie,
    QPainter,
    QFontMetrics,
)

from thumb_loader import thumb_path, unescape_map_name

from ui_delegates import TwoLineDelegate  # 新增


from map_meta import (
    map_meta_memory,
    load_map_meta_cache,
)

from map_crawler import MapCrawler, PRIO_VISIBLE, PRIO_NEAR, PRIO_BULK

MASTER_URLS = [
    "https://master1.ddnet.org/ddnet/15/servers.json",
    "https://master2.ddnet.org/ddnet/15/servers.json",
    "https://master3.ddnet.org/ddnet/15/servers.json",
]
PLAYER_URL_TEMPLATE = "https://ddnet.org/players/?json2={}"
REFRESH_INTERVAL_MS = 10_000  # 默认值，实际以 res/settings.json 为准
DDNET_DEFAULT_PORT = 8303
MAP_WEB_BASE = "https://teeworlds.cn/ddnet/maps#name="

ONLINE_COLOR = QColor("#2ecc71")
OFFLINE_COLOR = QColor("#e74c3c")
FAV_PLAYER_COLOR = QColor("#f39c12")
# ---------- 类型中文提示 ----------
TYPE_NAMES_ZH = {
    "Novice": "简单",
    "Moderate": "中阶",
    "Brutal": "高阶",
    "Insane": "疯狂",
    "Dummy": "分身",
    "DDmaX": "DDmaX 经典",
    "Oldschool": "旧的",
    "Solo": "单人",
    "Race": "竞速",
    "Fun": "娱乐",
}


TYPE_DESCRIPTIONS = {
    "Novice": "The simplest maps for beginners.",
    "Moderate": "Moderate maps for intermediate players.",
    "Brutal": "Challenging maps for advanced players.",
    "Insane": "Very difficult maps for experts.",
    "Dummy": "A dummy (second tee) becomes deep-frozen; move it to the finish.",
    "DDmaX": "Maps that existed on DDracemaX (2009–2014).",
    "Oldschool": "Old non-DDmaX maps that existed before DDNet.",
    "Solo": "Play alone. No dummies or interference with other users.",
    "Race": "Race mode, hook/unhookable tiles, grenade launcher movement.",
    "Fun": "Maps to have fun (no points).",
}


TYPE_DESCRIPTIONS_ZH = {
    "Novice": "最简单的地图，适合刚入门的玩家。",
    "Moderate": "中等难度，适合有一定基础的玩家。",
    "Brutal": "高难度地图，面向进阶玩家。",
    "Insane": "极高难度地图，面向顶尖玩家。",
    "Dummy": "需要操控分身（第二只 Tee）并把它带到终点。",
    "DDmaX": "2009–2014 年 DDracemaX 服务器上的经典地图。",
    "Oldschool": "DDNet 出现之前的旧地图。",
    "Solo": "单人地图，没有分身，也不会被其他玩家干扰。",
    "Race": "竞速模式，依赖钩子/解钩和手雷移动。",
    "Fun": "娱乐地图，不计分数。",
}


def map_tooltip(map_name, meta=None):
    if not meta:
        return map_name
    diff = meta.get("difficulty")
    stars = (
        "★" * diff + "☆" * (5 - diff) if isinstance(diff, int) and diff >= 0 else "?"
    )
    return "\n".join(
        [
            map_name,
            f"类型: {type_label(meta.get('type'))}",
            f"难度: {stars}",
            f"分数: {meta.get('points', '?')}",
            "",
            TYPE_DESCRIPTIONS_ZH.get(meta.get("type"), ""),
        ]
    ).strip()


def type_label(t):
    """显示用：Novice（新手）"""
    if not t:
        return "?"
    zh = TYPE_NAMES_ZH.get(t)
    return f"{t}（{zh}）" if zh else str(t)


def type_tooltip(t):
    """悬停提示：中文说明为主，英文原文附在最后"""
    if not t:
        return ""
    zh_name = TYPE_NAMES_ZH.get(t, "")
    zh_desc = TYPE_DESCRIPTIONS_ZH.get(t, "")
    en_desc = TYPE_DESCRIPTIONS.get(t, "")
    head = f"{t} — {zh_name}" if zh_name else str(t)
    parts = [head]
    if zh_desc:
        parts.append(zh_desc)
    if en_desc:
        parts.append(f"English: {en_desc}")
    return "\n".join(parts)


# ---------- 收藏文件位置 ----------
def _get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


RES_DIR = os.path.join(_get_app_dir(), "res")
try:
    os.makedirs(RES_DIR, exist_ok=True)
except Exception:
    pass

LOADING_GIF = os.path.join(RES_DIR, "loading.gif")


def _get_fav_file():
    """收藏文件：res/favorites.json；旧位置有文件会自动迁移一次"""
    target = os.path.join(RES_DIR, "favorites.json")
    try:
        os.makedirs(RES_DIR, exist_ok=True)
        test = os.path.join(RES_DIR, ".write_test")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)

        # 迁移：旧位置 → res/
        if not os.path.exists(target):
            legacy = [
                os.path.join(_get_app_dir(), "favorites.json"),
                os.path.join(
                    os.path.expanduser("~"), ".ddnet_monitor", "favorites.json"
                ),
            ]
            for old in legacy:
                if os.path.exists(old):
                    shutil.copy2(old, target)
                    break
        return target
    except Exception:
        user_dir = os.path.join(os.path.expanduser("~"), ".ddnet_monitor")
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, "favorites.json")


FAV_FILE = _get_fav_file()
SETTINGS_FILE = os.path.join(os.path.dirname(FAV_FILE), "settings.json")


def _load_favorites():
    if os.path.exists(FAV_FILE):
        try:
            with open(FAV_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (
                set(data.get("players", [])),
                {unescape_map_name(m) for m in data.get("maps", [])},
            )
        except Exception:
            pass
    return set(), set()


def _save_favorites(players, maps):
    try:
        with open(FAV_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"players": sorted(players), "maps": sorted(maps)},
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


# ---------- 后台线程 ----------
class FetchWorker(QThread):
    data_ready = pyqtSignal(dict, dict, float)
    error_occurred = pyqtSignal(str)

    def __init__(self, player_name):
        super().__init__()
        self.player_name = player_name

    def run(self):
        try:
            t0 = time.perf_counter()
            servers = self._fetch_servers()
            player = self._fetch_player(self.player_name)
            elapsed = time.perf_counter() - t0
            self.data_ready.emit(servers, player, elapsed)
        except Exception as e:
            self.error_occurred.emit(f"请求失败: {e}")

    @staticmethod
    def _fetch_servers():
        last_err = None
        for url in MASTER_URLS:
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_err = e
        raise last_err

    @staticmethod
    def _fetch_player(name):
        url = PLAYER_URL_TEMPLATE.format(quote(name, safe=""))
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()


class PlayerFetchWorker(QThread):
    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, player_name):
        super().__init__()
        self.player_name = player_name

    def run(self):
        try:
            url = PLAYER_URL_TEMPLATE.format(quote(self.player_name, safe=""))
            resp = requests.get(url, timeout=10)
            if resp.status_code == 404:
                self.error_occurred.emit(f"玩家 {self.player_name} 不存在")
                return
            resp.raise_for_status()
            data = resp.json()
            if not data or not data.get("player"):
                self.error_occurred.emit(f"玩家 {self.player_name} 没有数据")
                return
            self.data_ready.emit(data)
        except Exception as e:
            self.error_occurred.emit(f"请求失败: {e}")


class PingWorker(QThread):
    ping_result = pyqtSignal(str, float)
    progress = pyqtSignal(int, int)
    finished_all = pyqtSignal()

    def __init__(self, addresses, timeout=1.2, workers=40):
        super().__init__()
        self.addresses = addresses
        self.timeout = timeout
        self.workers = workers
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ex = ThreadPoolExecutor(max_workers=self.workers)
        try:
            futures = {ex.submit(self._tcp_ping, a): a for a in self.addresses}
            done = 0
            total = len(self.addresses)
            for fut in as_completed(futures):
                if self._cancel:
                    break
                addr = futures[fut]
                try:
                    ms = fut.result()
                except Exception:
                    ms = -1.0
                self.ping_result.emit(addr, ms)
                done += 1
                if done % 5 == 0 or done == total:
                    self.progress.emit(done, total)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
            self.finished_all.emit()

    def _tcp_ping(self, address):
        host, port = self._parse_address(address)
        if not host:
            return -1.0
        try:
            info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            if not info:
                return -1.0
            family, socktype, proto, _, sockaddr = info[0]
            start = time.perf_counter()
            with socket.socket(family, socktype, proto) as s:
                s.settimeout(self.timeout)
                s.connect(sockaddr)
            return (time.perf_counter() - start) * 1000.0
        except Exception:
            return -1.0

    @staticmethod
    def _parse_address(address):
        addr = address
        if "://" in addr:
            addr = urlparse(addr).netloc or addr.split("://", 1)[-1]
        if ":" in addr:
            host, port_str = addr.rsplit(":", 1)
            try:
                port = int(port_str.split("/")[0])
            except ValueError:
                port = DDNET_DEFAULT_PORT
            return host, port
        return addr, DDNET_DEFAULT_PORT


# ---------- 主窗口 ----------
class DDNetMonitor(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings or load_settings(SETTINGS_FILE)
        self.main_player = (self.settings.get("player") or "").strip()

        self.setWindowTitle("DDnetGI")
        self.resize(1980, 950)
        self._frame_count = 0
        QApplication.instance().installEventFilter(self)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("准备就绪")
        self.status_bar.addWidget(self.status_label)

        self.crawler_status_label = QLabel("")
        self.crawler_status_label.setStyleSheet("color: #888;")
        self.status_bar.addPermanentWidget(self.crawler_status_label)

        # ---- 数据 / 状态 ----
        self.servers_data = {}
        self.player_data = {}
        self.current_player = self.main_player
        self.server_ping = {}
        self._item_map = {}
        self._expanded_keys = set()
        self._selected_key = None
        self._sort_mode = "players"
        self._sort_desc = True
        self._ping_running = False
        self._last_shown_count = 0

        # 收藏
        self.fav_players, self.fav_maps = _load_favorites()
        self._online_players = {}
        self._map_servers = {}

        # 搜索
        self._search_keyword = ""
        self._search_matches = []
        self._search_index = -1
        self._search_tree_version = -1
        self._tree_version = 0

        # ---- 缩略图可见区懒加载（必须早于 right_tabs / 树创建）----
        self._thumb_requested = set()  # 已入队过的地图名
        self._broken_thumbs = set()  # 下载失败，显示“无图”
        self._visible_pending = []  # 视口内仍缺图的 (tree, item, col)
        self._loading_movie = None
        self._loading_pix = None
        self._loading_icon = QIcon()
        self._broken_icon = QIcon()

        self._pending_scan = QTimer(self)
        self._pending_scan.setSingleShot(True)
        self._pending_scan.setInterval(80)
        self._pending_scan.timeout.connect(self._apply_visible_icons)

        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(120)
        self._loading_timer.timeout.connect(self._tick_loading)
        self._loading_timer.start()

        self._init_loading_asset()

        # 名称过滤防抖
        self._name_filter_timer = QTimer(self)
        self._name_filter_timer.setSingleShot(True)
        self._name_filter_timer.setInterval(250)
        self._name_filter_timer.timeout.connect(self._apply_name_filter)

        # ---- 工具栏 ----
        toolbar = QWidget()
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(6, 6, 6, 6)

        tl.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("玩家/地图/IP 关键词")
        self.search_edit.setMinimumWidth(150)
        self.search_edit.returnPressed.connect(self._on_search)
        tl.addWidget(self.search_edit)

        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)
        tl.addWidget(self.search_btn)

        self.search_next_btn = QPushButton("下一个 ▶")
        self.search_next_btn.clicked.connect(self._on_search_next)
        tl.addWidget(self.search_next_btn)

        tl.addWidget(QLabel("名称过滤:"))
        self.name_filter_edit = QLineEdit()
        self.name_filter_edit.setPlaceholderText("服务器名（中/英文）")
        self.name_filter_edit.setMinimumWidth(170)
        self.name_filter_edit.setClearButtonEnabled(True)
        self.name_filter_edit.textChanged.connect(self._on_name_filter_text_changed)
        tl.addWidget(self.name_filter_edit)

        tl.addWidget(QLabel("类型:"))
        self.type_filter_combo = QComboBox()
        self.type_filter_combo.addItem("全部类型")
        self.type_filter_combo.setMinimumWidth(170)
        self.type_filter_combo.currentIndexChanged.connect(self._on_type_filter_changed)
        tl.addWidget(self.type_filter_combo)

        tl.addWidget(QLabel("排序:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(
            [
                "按在线人数",
                "按延迟",
                "按最大容量",
                "按服务器名称",
                "按地图名称",
            ]
        )
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tl.addWidget(self.sort_combo)

        self.sort_order_btn = QPushButton("↓ 降序")
        self.sort_order_btn.clicked.connect(self._toggle_sort_order)
        tl.addWidget(self.sort_order_btn)

        self.ping_btn = QPushButton("测延迟")
        self.ping_btn.clicked.connect(self._start_ping)
        tl.addWidget(self.ping_btn)

        self.settings_btn = QPushButton("⚙ 设置")
        self.settings_btn.clicked.connect(self._open_settings)
        tl.addWidget(self.settings_btn)

        # ---- 三栏 ----
        main_splitter = QSplitter(Qt.Horizontal)

        # ---- 左：服务器 / 玩家树 ----
        self.tree = QTreeWidget()  # ① 创建
        self.tree.setHeaderLabels(
            ["服务器 / 玩家", "地图 / Team", "人数", "分数", "延迟", "地址"]
        )
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 380)
        self.tree.setColumnWidth(2, 60)
        self.tree.setColumnWidth(3, 60)
        self.tree.setColumnWidth(4, 70)
        self.tree.setColumnWidth(5, 220)
        self.tree.setIconSize(QSize(80, 60))  # ② 原有
        self.tree.setUniformRowHeights(True)  # ③ 新增
        self.tree.setWordWrap(True)  # ③ 新增
        self.tree.setItemDelegate(
            TwoLineDelegate(
                self.tree,
                max_lines=2,
                min_height=60,
                elide_modes={1: Qt.ElideLeft},  # 地图列：省略前面，保留类型/分数
            )
        )

        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        main_splitter.addWidget(self.tree)

        # ---- 中：详情 ----
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Consolas", 10))
        main_splitter.addWidget(self.detail)

        # ---- 右侧面板容器 + 标题行 ----
        player_panel = QWidget()
        pl = QVBoxLayout(player_panel)
        pl.setContentsMargins(0, 0, 0, 0)

        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(4, 4, 4, 4)

        self.player_title = QLabel(f"👤 {self.main_player or '未设置玩家'} — 完整档案")
        self.player_title.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        title_layout.addWidget(self.player_title, 1)

        self.restore_btn = QPushButton(f"↩ 恢复 {self.main_player or '主玩家'}")
        self.restore_btn.clicked.connect(lambda: self._switch_player(self.main_player))
        self.restore_btn.setVisible(False)
        title_layout.addWidget(self.restore_btn)

        pl.addWidget(title_row)

        self.right_tabs = QTabWidget()
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        # ---- 档案树 ----
        self.player_tree = QTreeWidget()  # ① 创建（不能删）
        self.player_tree.setHeaderLabels(
            ["名称 / 类别", "排名", "分数 / 最大总分", "已完成地图"]
        )
        self.player_tree.setColumnWidth(0, 260)  # 原 180
        self.player_tree.setColumnWidth(1, 110)
        self.player_tree.setColumnWidth(2, 150)
        self.player_tree.setColumnWidth(3, 130)
        self.player_tree.setFont(QFont("Consolas", 9))
        self.player_tree.setIconSize(QSize(72, 48))  # 新增
        self.player_tree.setUniformRowHeights(True)  # 新增
        self.player_tree.setWordWrap(True)  # 新增
        self.player_tree.setItemDelegate(
            TwoLineDelegate(
                self.player_tree,
                max_lines=2,
                min_height=48,
                elide_modes={0: Qt.ElideLeft},
            )
        )

        self.player_tree.itemExpanded.connect(self._on_player_tree_expanded)  # 新增
        self.player_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.player_tree.customContextMenuRequested.connect(
            self._on_player_tree_context_menu
        )
        self.right_tabs.addTab(self.player_tree, "📊 档案")

        # Tab 1：收藏玩家
        self.fav_players_tree = QTreeWidget()
        self.fav_players_tree.setHeaderLabels(["玩家", "在线状态", "", ""])
        self.fav_players_tree.setColumnWidth(0, 200)
        self.fav_players_tree.setColumnWidth(1, 140)
        self.fav_players_tree.setFont(QFont("Consolas", 9))
        self.fav_players_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fav_players_tree.customContextMenuRequested.connect(
            self._on_fav_players_context_menu
        )
        self.fav_players_tree.itemDoubleClicked.connect(
            lambda it, col: self._goto_player_in_tree(it.data(0, Qt.UserRole) or "")
        )
        self.right_tabs.addTab(self.fav_players_tree, "⭐ 收藏玩家")

        # Tab 2：收藏地图
        self.fav_maps_tree = QTreeWidget()
        self.fav_maps_tree.setHeaderLabels(["地图", "运行状态", "", ""])
        self.fav_maps_tree.setColumnWidth(0, 220)
        self.fav_maps_tree.setColumnWidth(1, 140)
        self.fav_maps_tree.setFont(QFont("Consolas", 9))
        self.fav_maps_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fav_maps_tree.customContextMenuRequested.connect(
            self._on_fav_maps_context_menu
        )
        self.fav_maps_tree.itemDoubleClicked.connect(
            lambda it, col: self._goto_map_in_tree(it.data(0, Qt.UserRole) or "")
        )
        self.right_tabs.addTab(self.fav_maps_tree, "🗺 收藏地图")

        pl.addWidget(self.right_tabs)
        main_splitter.addWidget(player_panel)

        main_splitter.setSizes([700, 280, 780])

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)

        # ---- 顶部中间：帧率 ----
        top_bar = QWidget()
        tbl = QHBoxLayout(top_bar)
        tbl.setContentsMargins(0, 2, 0, 0)
        tbl.addStretch(1)
        self.fps_label = QLabel("FPS: --")
        self.fps_label.setStyleSheet("color: #888; font-size: 11px;")
        tbl.addWidget(self.fps_label)
        tbl.addStretch(1)
        cl.addWidget(top_bar)

        cl.addWidget(toolbar)
        cl.addWidget(main_splitter, 1)
        self.setCentralWidget(central)

        self._update_title()

        # ---- 地图缩略图 / 元数据状态 ----
        load_map_meta_cache()
        self._thumb_cache = {}  # map_name -> QPixmap
        self._thumb_icons = {}  # map_name -> QIcon（复用，避免每次 new）
        self._ptree_items = {}  # map_name -> [档案树里的 QTreeWidgetItem]
        self._fetching = False  # 防止 10 秒定时器把请求堆叠
        self._player_sig = None  # 档案数据指纹，没变就不重建树

        for tv in (self.tree, self.player_tree):
            tv.verticalScrollBar().valueChanged.connect(self._on_any_scroll)
            tv.itemExpanded.connect(self._on_any_scroll)
            tv.itemCollapsed.connect(self._on_any_scroll)

        self._map_items = {}  # map_name -> [QTreeWidgetItem, ...]
        self._create_crawler()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.start_fetch)
        self._refresh_ms = int(self.settings.get("refresh_sec", 10)) * 1000
        self.timer.start(self._refresh_ms)

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.start()

        self.start_fetch()

    # ========== 标题 / tab ==========
    def _on_right_tab_changed(self, idx):
        self._update_title()
        self._pending_scan.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._pending_scan.start()

    # ========== 爬虫 / 设置 ==========
    def _create_crawler(self):
        gap = float(self.settings.get("thumb_gap", 0.30))
        workers = int(self.settings.get("thumb_workers", 3))
        self.crawler = MapCrawler(min_interval=gap, max_interval=gap, workers=workers)
        self.crawler.meta_ready.connect(self._on_meta_ready)
        self.crawler.meta_failed.connect(self._on_meta_failed)
        self.crawler.thumb_ready.connect(self._on_thumb_ready)
        self.crawler.thumb_failed.connect(self._on_thumb_failed)
        self.crawler.queue_size.connect(self._on_crawler_queue)
        self.crawler.start()

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        new = dlg.values()
        save_settings(SETTINGS_FILE, new)

        old_player = self.main_player
        self.settings = new
        self.main_player = new["player"]

        # 刷新间隔
        self._refresh_ms = int(new["refresh_sec"]) * 1000
        self.timer.setInterval(self._refresh_ms)

        # 重启爬虫（并发/间隔可能变了）
        try:
            self.crawler.stop()
            self.crawler.wait(3000)
        except Exception:
            pass
        self._create_crawler()

        # 玩家变了 → 重新加载档案
        if self.main_player and self.main_player != old_player:
            self.current_player = self.main_player
            self._switch_player(self.main_player)
        else:
            self._update_title()

        self.status_label.setText("设置已保存")

    # ========== FPS ==========
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Paint:
            self._frame_count += 1
        return super().eventFilter(obj, event)

    def _update_fps(self):
        frames = self._frame_count
        self._frame_count = 0
        self.fps_label.setText(f"FPS: {frames}")

    def _update_title(self):
        idx = self.right_tabs.currentIndex()
        if idx == 0:
            self.player_title.setText(f"👤 {self.current_player} — 完整档案")
            self.restore_btn.setVisible(
                bool(self.main_player) and self.current_player != self.main_player
            )
        elif idx == 1:
            self.player_title.setText(f"⭐ 收藏玩家 ({len(self.fav_players)})")
            self.restore_btn.setVisible(False)
        else:
            self.player_title.setText(f"🗺 收藏地图 ({len(self.fav_maps)})")
            self.restore_btn.setVisible(False)

    # ========== 数据拉取 ==========
    def start_fetch(self):
        if self._fetching:
            return
        self._fetching = True
        self.status_label.setText("正在刷新...")
        self.worker = FetchWorker(self.current_player)
        self.worker.data_ready.connect(self._on_data_ready)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.start()

    def _on_data_ready(self, servers, player, elapsed):
        if (
            player.get("player") != self.current_player
            and self.current_player != self.main_player
        ):
            self._fetching = False
            return

        self.servers_data = servers
        self._update_type_filter_options()  # ← 新增：每次拿到新数据重建类型列表
        self._refresh_server_tree()
        self._build_online_index()

        sig = hash(json.dumps(player, sort_keys=True, ensure_ascii=False))
        if sig != self._player_sig:
            self._player_sig = sig
            self.player_data = player
            self._refresh_player_tree()

        self._refresh_fav_players()
        self._refresh_fav_maps()
        self._fetching = False

        total = len(servers.get("servers", []))
        shown = self._last_shown_count
        filter_note = (
            f"过滤后 {shown}/{total}" if shown != total else f"服务器: {total}"
        )
        self.status_label.setText(
            f"{filter_note}  |  耗时: {elapsed:.2f}s  |  "
            f"更新: {datetime.now().strftime('%H:%M:%S')}"
        )

    def _on_error(self, msg):
        self._fetching = False
        self.status_label.setText(msg)

    # ========== 在线索引 ==========
    def _build_online_index(self):
        self._online_players.clear()
        self._map_servers.clear()
        for srv in self.servers_data.get("servers", []):
            info = srv.get("info", srv)
            sk = self._server_key(srv)
            mi = info.get("map", {})
            mname = mi.get("name", "") if isinstance(mi, dict) else ""
            if mname:
                self._map_servers.setdefault(mname, []).append(sk)
            for c in info.get("clients", []):
                if isinstance(c, dict) and c.get("is_player"):
                    pname = c.get("name", "")
                    if pname:
                        self._online_players.setdefault(pname, []).append((sk, info))

    # ========== 切换玩家 ==========
    def _switch_player(self, name):
        if not name:
            return
        if name == self.current_player:
            self.right_tabs.setCurrentIndex(0)
            return
        self.current_player = name
        self._update_title()
        self.status_label.setText(f"正在加载玩家 {name} 的数据...")

        self.player_worker = PlayerFetchWorker(name)
        self.player_worker.data_ready.connect(self._on_player_data_ready)
        self.player_worker.error_occurred.connect(self._on_player_data_error)
        self.player_worker.start()
        self.right_tabs.setCurrentIndex(0)

    def _on_player_data_ready(self, data):
        self.player_data = data
        self._refresh_player_tree()
        self.status_label.setText(f"已加载玩家 {self.current_player} 的数据")

    def _on_player_data_error(self, msg):
        self.status_label.setText(msg)

    # ========== 收藏 ==========
    def _persist_favorites(self):
        _save_favorites(self.fav_players, self.fav_maps)

    def _toggle_fav_player(self, pname):
        if not pname:
            return
        if pname in self.fav_players:
            self.fav_players.discard(pname)
            msg = f"已取消收藏玩家 {pname}"
        else:
            self.fav_players.add(pname)
            msg = f"已收藏玩家 {pname}"
        self._persist_favorites()
        self._refresh_fav_players()
        self._update_title()
        self.status_label.setText(msg)

    def _toggle_fav_map(self, mname):
        if not mname:
            return
        if mname in self.fav_maps:
            self.fav_maps.discard(mname)
            msg = f"已取消收藏地图 {mname}"
        else:
            self.fav_maps.add(mname)
            msg = f"已收藏地图 {mname}"
        self._persist_favorites()
        self._refresh_fav_maps()
        self._update_title()
        self.status_label.setText(msg)

    def _refresh_fav_players(self):
        existing = {}
        for i in range(self.fav_players_tree.topLevelItemCount()):
            it = self.fav_players_tree.topLevelItem(i)
            n = it.data(0, Qt.UserRole)
            if n:
                existing[n] = it
        for n, it in list(existing.items()):
            if n not in self.fav_players:
                idx = self.fav_players_tree.indexOfTopLevelItem(it)
                if idx >= 0:
                    self.fav_players_tree.takeTopLevelItem(idx)
                del existing[n]
        for pname in sorted(self.fav_players):
            locs = self._online_players.get(pname, [])
            is_online = len(locs) > 0
            text = f"🟢 {pname}" if is_online else f"🔴 {pname}"
            status = f"在线 · {len(locs)} 台" if is_online else "离线"
            color = ONLINE_COLOR if is_online else OFFLINE_COLOR

            if pname in existing:
                it = existing[pname]
                it.setText(0, text)
                it.setText(1, status)
                it.setForeground(0, color)
                it.setForeground(1, color)
                if is_online:
                    it.setToolTip(
                        0, "\n".join(info.get("name", "?") for _, info in locs)
                    )
                else:
                    it.setToolTip(0, "")
            else:
                it = QTreeWidgetItem([text, status, "", ""])
                it.setForeground(0, color)
                it.setForeground(1, color)
                it.setData(0, Qt.UserRole, pname)
                if is_online:
                    it.setToolTip(
                        0, "\n".join(info.get("name", "?") for _, info in locs)
                    )
                self.fav_players_tree.addTopLevelItem(it)

    def _refresh_fav_maps(self):
        existing = {}
        for i in range(self.fav_maps_tree.topLevelItemCount()):
            it = self.fav_maps_tree.topLevelItem(i)
            n = it.data(0, Qt.UserRole)
            if n:
                existing[n] = it
        for n, it in list(existing.items()):
            if n not in self.fav_maps:
                idx = self.fav_maps_tree.indexOfTopLevelItem(it)
                if idx >= 0:
                    self.fav_maps_tree.takeTopLevelItem(idx)
                del existing[n]
        for mname in sorted(self.fav_maps):
            sks = self._map_servers.get(mname, [])
            is_active = len(sks) > 0
            text = f"🟢 {mname}" if is_active else f"🔴 {mname}"
            status = f"运行中 · {len(sks)} 台" if is_active else "未运行"
            color = ONLINE_COLOR if is_active else OFFLINE_COLOR

            if mname in existing:
                it = existing[mname]
                it.setText(0, text)
                it.setText(1, status)
                it.setForeground(0, color)
                it.setForeground(1, color)
            else:
                it = QTreeWidgetItem([text, status, "", ""])
                it.setForeground(0, color)
                it.setForeground(1, color)
                it.setData(0, Qt.UserRole, mname)
                self.fav_maps_tree.addTopLevelItem(it)

    # ========== 跳转 ==========
    def _goto_player_in_tree(self, pname):
        if not pname:
            return
        locs = self._online_players.get(pname, [])
        if not locs:
            self.status_label.setText(f"玩家 {pname} 当前不在线")
            return
        for sk, _ in locs:
            key = f"{sk}|PLAYER|{pname}"
            item = self._item_map.get(key)
            if item:
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item, QTreeWidget.PositionAtCenter)
                self.status_label.setText(f"已定位玩家 {pname}")
                return
        self.status_label.setText(
            f"玩家 {pname} 在线，但被当前过滤条件隐藏，请清除过滤"
        )

    def _goto_map_in_tree(self, mname):
        if not mname:
            return
        sks = self._map_servers.get(mname, [])
        if not sks:
            self.status_label.setText(f"地图 {mname} 当前无服务器运行")
            return
        for sk in sks:
            item = self._item_map.get(sk)
            if item:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item, QTreeWidget.PositionAtCenter)
                self.status_label.setText(f"已定位到运行 {mname} 的服务器")
                return
        self.status_label.setText(f"地图 {mname} 有服务器运行，但被过滤条件隐藏")

    # ========== 名称过滤 ==========
    def _on_name_filter_text_changed(self, text):
        self._name_filter_timer.start()

    def _apply_name_filter(self):
        if self.servers_data:
            self._refresh_server_tree()
            self._build_online_index()
            shown = self._last_shown_count
            total = len(self.servers_data.get("servers", []))
            if shown != total:
                self.status_label.setText(f"名称过滤生效：{shown}/{total} 个服务器")
            else:
                self.status_label.setText(f"名称过滤：{total} 个服务器全部匹配")

    # ========== 类型过滤 ==========
    def _update_type_filter_options(self):
        game_types = set()
        for srv in self.servers_data.get("servers", []):
            info = srv.get("info", srv)
            gt = info.get("game_type", "")
            if gt:
                game_types.add(gt)
        current = self.type_filter_combo.currentText()
        self.type_filter_combo.blockSignals(True)
        self.type_filter_combo.clear()
        self.type_filter_combo.addItem("全部类型")

        # 1) DDraceNetwork 单独一项
        if any((gt or "").lower() == "ddracenetwork" for gt in game_types):
            self.type_filter_combo.addItem("DDraceNetwork")

        # 2) 其他每个 game_type 各一项（按字母序）
        others = sorted(
            (gt for gt in game_types if (gt or "").lower() != "ddracenetwork"),
            key=lambda s: s.lower(),
        )
        for gt in others:
            self.type_filter_combo.addItem(gt)

        # 3) 非 DDraceNetwork 汇总一项
        if others:
            self.type_filter_combo.addItem("非 DDraceNetwork")

        idx = self.type_filter_combo.findText(current)
        self.type_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.type_filter_combo.blockSignals(False)

    def _on_type_filter_changed(self, index):
        if self.servers_data:
            self._refresh_server_tree()
            self._build_online_index()

    # ========== key / 排序 ==========
    @staticmethod
    def _server_key(srv):
        addresses = srv.get("addresses", [])
        if addresses:
            return f"ADDR:{addresses[0]}"
        info = srv.get("info", {})
        mi = info.get("map", {}) if isinstance(info.get("map"), dict) else {}
        return f"NAME:{info.get('name', '?')}|{mi.get('name', '')}"

    def _sorted_servers(self):
        servers = list(self.servers_data.get("servers", []))
        filter_type = self.type_filter_combo.currentText()
        if filter_type and filter_type != "全部类型":
            if filter_type == "非 DDraceNetwork":
                servers = [
                    s
                    for s in servers
                    if (s.get("info", s).get("game_type", "") or "").lower()
                    != "ddracenetwork"
                ]
            else:
                servers = [
                    s
                    for s in servers
                    if (s.get("info", s).get("game_type", "") or "").lower()
                    == filter_type.lower()
                ]
        name_kw = self.name_filter_edit.text().strip().lower()
        if name_kw:
            servers = [
                s
                for s in servers
                if name_kw in (s.get("info", s).get("name", "") or "").lower()
            ]

        def player_count(srv):
            info = srv.get("info", srv)
            return len([c for c in info.get("clients", []) if c.get("is_player")])

        def latency(srv):
            addrs = srv.get("addresses", [])
            if not addrs:
                return float("inf")
            ms = self.server_ping.get(addrs[0], -1)
            return ms if ms and ms > 0 else float("inf")

        def name_key(srv):
            return (srv.get("info", srv).get("name", "") or "").lower()

        def cap_key(srv):
            info = srv.get("info", srv)
            try:
                return int(info.get("max_players", 0) or 0)
            except (TypeError, ValueError):
                return 0

        def map_key(srv):
            info = srv.get("info", srv)
            mi = info.get("map", {})
            name = mi.get("name", "") if isinstance(mi, dict) else ""
            return (name or "").lower()

        if self._sort_mode == "players":
            # 人数相同再按服务器名，避免每次刷新顺序乱跳
            servers.sort(
                key=lambda s: (player_count(s), name_key(s)),
                reverse=self._sort_desc,
            )
        elif self._sort_mode == "latency":
            servers.sort(key=latency, reverse=self._sort_desc)
        elif self._sort_mode == "capacity":
            servers.sort(
                key=lambda s: (cap_key(s), player_count(s)),
                reverse=self._sort_desc,
            )
        elif self._sort_mode == "map":
            servers.sort(
                key=lambda s: (map_key(s), name_key(s)),
                reverse=self._sort_desc,
            )
        else:  # "name"
            servers.sort(key=name_key, reverse=self._sort_desc)
        return servers

    def _on_sort_changed(self, index):
        # 顺序必须和 sort_combo.addItems 一一对应
        modes = ["players", "latency", "capacity", "name", "map"]
        self._sort_mode = modes[index] if 0 <= index < len(modes) else "players"
        # 人数 / 容量 / 延迟默认降序；名称 / 地图默认升序
        self._sort_desc = self._sort_mode in ("players", "capacity", "latency")
        self._update_sort_btn()
        self._refresh_server_tree()

    def _toggle_sort_order(self):
        self._sort_desc = not self._sort_desc
        self._update_sort_btn()
        self._refresh_server_tree()

    def _update_sort_btn(self):
        self.sort_order_btn.setText("↓ 降序" if self._sort_desc else "↑ 升序")

    # ========== 服务器树 ==========
    def _refresh_server_tree(self):
        self._save_tree_state()
        filtered = self._sorted_servers()
        self._last_shown_count = len(filtered)

        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            self._item_map.clear()
            self._map_items.clear()
            for srv in filtered:
                self._add_server_node(srv)
        finally:
            # 无论是否异常，必须恢复渲染，否则界面永久冻住
            self._restore_tree_state()
            self.tree.setUpdatesEnabled(True)

        self._tree_version += 1
        self._pending_scan.start()  # ← 新增：建完树后重算可见区

    def _add_server_node(self, srv):
        info = srv.get("info", srv)
        name = info.get("name") or "未命名服务器"
        mi = info.get("map", {})
        map_name = mi.get("name", "") if isinstance(mi, dict) else ""
        addresses = srv.get("addresses", [])
        address = addresses[0] if addresses else ""
        clients = info.get("clients", []) or []
        players = [
            c for c in clients if isinstance(c, dict) and c.get("is_player", False)
        ]
        max_players = info.get("max_players", 0)
        server_key = self._server_key(srv)
        ms = self.server_ping.get(address, None)
        lat = f"{ms:.0f} ms" if ms and ms > 0 else ("超时" if ms == -1 else "")

        item = QTreeWidgetItem()
        item.setText(0, name)
        item.setText(2, f"{len(players)}/{max_players}")
        item.setText(4, lat)
        item.setText(5, address)
        item.setData(0, Qt.UserRole, server_key)
        item.setData(
            0, Qt.UserRole + 1, {"type": "server", "info": info, "address": address}
        )
        f = item.font(0)
        f.setBold(True)
        item.setFont(0, f)
        self.tree.addTopLevelItem(item)
        self._item_map[server_key] = item

        # ---- 地图列：只写文字 + 登记；图标等进入视口再挂 ----
        game_type = (info.get("game_type") or "").strip()
        is_ddnet = game_type.lower() == "ddracenetwork"

        if map_name:
            if not is_ddnet:
                # 非 DDraceNetwork 服务器：ddnet.org 没有数据，不爬不存
                item.setText(1, self._non_ddnet_text(map_name))
                item.setToolTip(
                    1,
                    f"{map_name}\n服务器类型：{game_type or '未知'}\n"
                    "非 DDraceNetwork 服务器，ddnet.org 无此地图的类型/分数/缩略图",
                )
                self._apply_non_ddnet_style(item)
            else:
                meta = map_meta_memory.get(map_name)
                item.setText(1, self._map_cell_text(map_name, meta))
                item.setToolTip(1, map_tooltip(map_name, meta))
                item.setData(0, Qt.UserRole + 2, map_name)  # 可见区渲染用
                self._map_items.setdefault(map_name, []).append(item)

                if meta is None or not meta.get("thumbnail"):
                    self.crawler.enqueue_meta(map_name, priority=PRIO_BULK)
                self._request_thumb(map_name, priority=PRIO_BULK)

        players.sort(key=lambda c: c.get("score", 0), reverse=True)
        for cli in players:
            self._add_player_node(item, cli, info, address, server_key)

    @staticmethod
    def _apply_non_ddnet_style(item):
        """非 DDraceNetwork 地图：第 2 列红色小字"""
        try:
            f = item.font(1)
            size = f.pointSizeF()
            if size > 0:
                f.setPointSizeF(max(7.0, size - 1.5))
            else:
                px = f.pixelSize()
                if px > 0:
                    f.setPixelSize(max(9, px - 2))
            item.setFont(1, f)
            item.setForeground(1, QColor("#e74c3c"))
        except RuntimeError:
            pass

    def _non_ddnet_text(self, map_name):
        """非 DDNet 地图：标签直接接在地图名后面，由 delegate 自动换行"""
        return f"{map_name}（非 DDraceNetwork）"

    def _map_cell_text(self, map_name, meta=None, game_type=None):
        """列 1 的文字：地图名 | 类型(中文) | 分数 | 星级
        meta 为 None = 还没爬；meta 里没有 type = 不是 DDNet 地图
        """
        if meta is None:
            if game_type and game_type.lower() != "ddracenetwork":
                return self._non_ddnet_text(map_name)
            return map_name
        mtype = meta.get("type")
        if not mtype:
            return self._non_ddnet_text(map_name)
        points = meta.get("points", "?")
        diff = meta.get("difficulty")
        if isinstance(diff, int) and diff >= 0:
            stars = "★" * diff + "☆" * (5 - diff)
        else:
            stars = "?"
        return f"{map_name}  |  {type_label(mtype)}  |  {points}分  |  {stars}"

    def _add_player_node(self, parent_item, cli, srv_info, address, server_key):
        pname = cli.get("name", "?")
        score = cli.get("score", 0)
        team = cli.get("team", 0)

        p_item = QTreeWidgetItem()
        p_item.setText(0, f"  {pname}")
        if team:
            p_item.setText(1, f"Team {team}")
        p_item.setText(3, str(score))

        if pname and pname == self.main_player:
            p_item.setForeground(0, QColor("#FF6B6B"))
            f = p_item.font(0)
            f.setBold(True)
            p_item.setFont(0, f)
        elif pname in self.fav_players:
            p_item.setForeground(0, FAV_PLAYER_COLOR)
            f = p_item.font(0)
            f.setItalic(True)
            p_item.setFont(0, f)

        player_key = f"{server_key}|PLAYER|{pname}"
        p_item.setData(0, Qt.UserRole, player_key)
        p_item.setData(
            0,
            Qt.UserRole + 1,
            {"type": "player", "data": cli, "server": srv_info, "address": address},
        )
        parent_item.addChild(p_item)
        self._item_map[player_key] = p_item

    # ========== 折叠状态 ==========
    def _save_tree_state(self):
        self._expanded_keys.clear()

        def walk(item):
            if item.isExpanded():
                k = item.data(0, Qt.UserRole)
                if k:
                    self._expanded_keys.add(k)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

        cur = self.tree.currentItem()
        self._selected_key = cur.data(0, Qt.UserRole) if cur else None

    def _restore_tree_state(self):
        def expand(item):
            k = item.data(0, Qt.UserRole)
            if k and k in self._expanded_keys:
                item.setExpanded(True)
            for i in range(item.childCount()):
                expand(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            expand(self.tree.topLevelItem(i))

        if self._selected_key and self._selected_key in self._item_map:
            it = self._item_map[self._selected_key]
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)

        self._apply_expanded_state()

    def _apply_expanded_state(self):
        def apply(item):
            k = item.data(0, Qt.UserRole)
            if k:
                item.setExpanded(k in self._expanded_keys)
            for i in range(item.childCount()):
                apply(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            apply(self.tree.topLevelItem(i))

    @staticmethod
    def _collapse_tree(tree):
        tree.setUpdatesEnabled(False)
        try:

            def walk(it):
                it.setExpanded(False)
                for j in range(it.childCount()):
                    walk(it.child(j))

            for i in range(tree.topLevelItemCount()):
                walk(tree.topLevelItem(i))
        finally:
            tree.setUpdatesEnabled(True)

    @staticmethod
    def _collapse_item_at(tree, pos):
        """点击时才重新取 item，避免持有已被 clear() 销毁的对象"""
        try:
            item = tree.itemAt(pos)
            if item is not None:
                item.setExpanded(False)
        except RuntimeError:
            pass

    def _collapse_all_tree(self):
        self._collapse_tree(self.tree)
        self._pending_scan.start()

    def _collapse_all_player_tree(self):
        self._collapse_tree(self.player_tree)
        self._pending_scan.start()

    # ========== 主树右键 ==========
    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        payload = item.data(0, Qt.UserRole + 1) if item is not None else None

        menu = QMenu(self)

        # 折叠操作：点在空白处也能用「全部折叠」
        act = menu.addAction("📁 全部折叠")
        act.triggered.connect(self._collapse_all_tree)
        if item is not None:
            act = menu.addAction("📂 折叠当前项")
            act.triggered.connect(
                lambda checked=False, p=pos: self._collapse_item_at(self.tree, p)
            )
        if payload:
            menu.addSeparator()

        if payload and payload.get("type") == "server":
            info = payload["info"]
            mi = info.get("map", {})
            map_name = mi.get("name", "") if isinstance(mi, dict) else ""
            if map_name:
                act = menu.addAction(f"🌐 查看此地图: {map_name}")
                act.triggered.connect(
                    lambda checked=False, m=map_name: self._open_map(m)
                )
                label = (
                    "❌ 取消收藏地图" if map_name in self.fav_maps else "⭐ 收藏此地图"
                )
                act = menu.addAction(f"{label}: {map_name}")
                act.triggered.connect(
                    lambda checked=False, m=map_name: self._toggle_fav_map(m)
                )
                act = menu.addAction(f"📋 复制地图名: {map_name}")
                act.triggered.connect(
                    lambda checked=False, m=map_name: self._copy_text(m)
                )
            addr = payload.get("address", "")
            if addr:
                act = menu.addAction("📋 复制服务器地址")
                act.triggered.connect(lambda checked=False, a=addr: self._copy_text(a))
            name = info.get("name", "")
            if name:
                act = menu.addAction("📋 复制服务器名")
                act.triggered.connect(lambda checked=False, n=name: self._copy_text(n))

        elif payload.get("type") == "player":
            cli = payload["data"]
            info = payload["server"]
            mi = info.get("map", {})
            map_name = mi.get("name", "") if isinstance(mi, dict) else ""
            pname = cli.get("name", "")
            if pname:
                act = menu.addAction(f"🔍 查看玩家信息: {pname}")
                act.triggered.connect(
                    lambda checked=False, n=pname: self._switch_player(n)
                )
                label = (
                    "❌ 取消收藏玩家" if pname in self.fav_players else "⭐ 收藏此玩家"
                )
                act = menu.addAction(f"{label}: {pname}")
                act.triggered.connect(
                    lambda checked=False, n=pname: self._toggle_fav_player(n)
                )
            if map_name:
                act = menu.addAction(f"🌐 查看所在服务器地图: {map_name}")
                act.triggered.connect(
                    lambda checked=False, m=map_name: self._open_map(m)
                )
                label = (
                    "❌ 取消收藏地图" if map_name in self.fav_maps else "⭐ 收藏此地图"
                )
                act = menu.addAction(f"{label}: {map_name}")
                act.triggered.connect(
                    lambda checked=False, m=map_name: self._toggle_fav_map(m)
                )
                act = menu.addAction(f"📋 复制地图名: {map_name}")
                act.triggered.connect(
                    lambda checked=False, m=map_name: self._copy_text(m)
                )
            if pname:
                act = menu.addAction(f"📋 复制玩家名: {pname}")
                act.triggered.connect(lambda checked=False, n=pname: self._copy_text(n))

        if menu.actions():
            menu.exec_(self.tree.viewport().mapToGlobal(pos))

    # ========== 档案树右键 ==========
    def _on_player_tree_context_menu(self, pos):
        item = self.player_tree.itemAt(pos)
        menu = QMenu(self)

        act = menu.addAction("📁 全部折叠")
        act.triggered.connect(self._collapse_all_player_tree)
        if item is not None:
            act = menu.addAction("📂 折叠当前项")
            act.triggered.connect(
                lambda checked=False, p=pos: self._collapse_item_at(self.player_tree, p)
            )

        if item is None:
            menu.exec_(self.player_tree.viewport().mapToGlobal(pos))
            return

        key = item.data(0, Qt.UserRole) or ""
        menu.addSeparator()

        if key.startswith("map:"):
            map_name = key[4:]
            act = menu.addAction(f"🌐 查看此地图: {map_name}")
            act.triggered.connect(lambda checked=False, m=map_name: self._open_map(m))
            label = "❌ 取消收藏地图" if map_name in self.fav_maps else "⭐ 收藏此地图"
            act = menu.addAction(label)
            act.triggered.connect(
                lambda checked=False, m=map_name: self._toggle_fav_map(m)
            )
            act = menu.addAction("📋 复制地图名")
            act.triggered.connect(lambda checked=False, m=map_name: self._copy_text(m))
        elif key.startswith("type:"):
            type_name = key[5:]
            act = menu.addAction(f"📋 复制类型名: {type_name}")
            act.triggered.connect(lambda checked=False, t=type_name: self._copy_text(t))
            act = menu.addAction("展开该类型全部地图")
            act.triggered.connect(lambda checked=False, i=item: i.setExpanded(True))
            act = menu.addAction("折叠该类型")
            act.triggered.connect(lambda checked=False, i=item: i.setExpanded(False))
        elif key == "overview":
            act = menu.addAction("📋 复制总览信息")
            act.triggered.connect(lambda checked=False, i=item: self._copy_overview(i))
        elif key == "recent":
            act = menu.addAction("📋 复制最近完成列表")
            act.triggered.connect(lambda checked=False, i=item: self._copy_subtree(i))
        elif key == "partners":
            act = menu.addAction("📋 复制常用伙伴列表")
            act.triggered.connect(lambda checked=False, i=item: self._copy_subtree(i))
        else:
            parent = item.parent()
            if parent:
                parent_key = parent.data(0, Qt.UserRole) or ""
                if parent_key.startswith("type:"):
                    map_name = item.text(0).strip()
                    if map_name:
                        act = menu.addAction(f"🌐 查看此地图: {map_name}")
                        act.triggered.connect(
                            lambda checked=False, m=map_name: self._open_map(m)
                        )
                        label = (
                            "❌ 取消收藏地图"
                            if map_name in self.fav_maps
                            else "⭐ 收藏此地图"
                        )
                        act = menu.addAction(f"{label}")
                        act.triggered.connect(
                            lambda checked=False, m=map_name: self._toggle_fav_map(m)
                        )
                        act = menu.addAction("📋 复制地图名")
                        act.triggered.connect(
                            lambda checked=False, m=map_name: self._copy_text(m)
                        )
                        act = menu.addAction("📋 复制该行完整信息")
                        act.triggered.connect(
                            lambda checked=False, i=item: self._copy_row(i)
                        )
                elif parent_key == "recent":
                    map_name = item.text(0).strip()
                    if map_name:
                        act = menu.addAction(f"🌐 查看此地图: {map_name}")
                        act.triggered.connect(
                            lambda checked=False, m=map_name: self._open_map(m)
                        )
                        label = (
                            "❌ 取消收藏地图"
                            if map_name in self.fav_maps
                            else "⭐ 收藏此地图"
                        )
                        act = menu.addAction(f"{label}")
                        act.triggered.connect(
                            lambda checked=False, m=map_name: self._toggle_fav_map(m)
                        )
                        act = menu.addAction("📋 复制地图名")
                        act.triggered.connect(
                            lambda checked=False, m=map_name: self._copy_text(m)
                        )
                    act = menu.addAction("📋 复制该行完整信息")
                    act.triggered.connect(
                        lambda checked=False, i=item: self._copy_row(i)
                    )
                elif parent_key == "partners":
                    partner = item.text(0).strip()
                    if partner:
                        act = menu.addAction(f"🔍 查看玩家信息: {partner}")
                        act.triggered.connect(
                            lambda checked=False, n=partner: self._switch_player(n)
                        )
                        label = (
                            "❌ 取消收藏玩家"
                            if partner in self.fav_players
                            else "⭐ 收藏此玩家"
                        )
                        act = menu.addAction(f"{label}")
                        act.triggered.connect(
                            lambda checked=False, n=partner: self._toggle_fav_player(n)
                        )
                        act = menu.addAction("📋 复制伙伴名")
                        act.triggered.connect(
                            lambda checked=False, n=partner: self._copy_text(n)
                        )
                    act = menu.addAction("📋 复制该行完整信息")
                    act.triggered.connect(
                        lambda checked=False, i=item: self._copy_row(i)
                    )
                elif parent_key in ("overview", "time"):
                    act = menu.addAction("📋 复制该行")
                    act.triggered.connect(
                        lambda checked=False, i=item: self._copy_row(i)
                    )

        if menu.actions():
            menu.exec_(self.player_tree.viewport().mapToGlobal(pos))

    # ========== 收藏面板右键 ==========
    def _on_fav_players_context_menu(self, pos):
        item = self.fav_players_tree.itemAt(pos)
        if not item:
            return
        pname = item.data(0, Qt.UserRole) or ""
        if not pname:
            return
        online = bool(self._online_players.get(pname))
        menu = QMenu(self)
        act = menu.addAction(f"🎯 跳转到位置: {pname}")
        act.triggered.connect(
            lambda checked=False, n=pname: self._goto_player_in_tree(n)
        )
        act.setEnabled(online)
        act = menu.addAction("🔍 查看玩家档案")
        act.triggered.connect(lambda checked=False, n=pname: self._show_profile(n))
        act = menu.addAction("📋 复制玩家名")
        act.triggered.connect(lambda checked=False, n=pname: self._copy_text(n))
        menu.addSeparator()
        act = menu.addAction("❌ 取消收藏")
        act.triggered.connect(lambda checked=False, n=pname: self._toggle_fav_player(n))
        menu.exec_(self.fav_players_tree.viewport().mapToGlobal(pos))

    def _on_fav_maps_context_menu(self, pos):
        item = self.fav_maps_tree.itemAt(pos)
        if not item:
            return
        mname = item.data(0, Qt.UserRole) or ""
        if not mname:
            return
        active = bool(self._map_servers.get(mname))
        menu = QMenu(self)
        act = menu.addAction(f"🎯 跳转到服务器: {mname}")
        act.triggered.connect(lambda checked=False, m=mname: self._goto_map_in_tree(m))
        act.setEnabled(active)
        act = menu.addAction("🌐 打开地图网页")
        act.triggered.connect(lambda checked=False, m=mname: self._open_map(m))
        act = menu.addAction("📋 复制地图名")
        act.triggered.connect(lambda checked=False, m=mname: self._copy_text(m))
        menu.addSeparator()
        act = menu.addAction("❌ 取消收藏")
        act.triggered.connect(lambda checked=False, m=mname: self._toggle_fav_map(m))
        menu.exec_(self.fav_maps_tree.viewport().mapToGlobal(pos))

    def _show_profile(self, pname):
        self._switch_player(pname)
        self.right_tabs.setCurrentIndex(0)

    # ========== 工具 ==========
    def _copy_overview(self, item):
        lines = []
        for i in range(item.childCount()):
            c = item.child(i)
            lines.append(f"{c.text(0).strip()}: {c.text(1)}")
        self._copy_text("\n".join(lines))

    def _copy_subtree(self, item):
        lines = []
        for i in range(item.childCount()):
            c = item.child(i)
            lines.append(
                " | ".join(c.text(j) for j in range(c.columnCount()) if c.text(j))
            )
        self._copy_text("\n".join(lines))

    def _copy_row(self, item):
        parts = [item.text(j) for j in range(item.columnCount()) if item.text(j)]
        self._copy_text(" | ".join(parts))

    def _open_map(self, map_name):
        if not map_name:
            return
        url = MAP_WEB_BASE + quote(map_name, safe="!$&'()*+,;=:@")
        QDesktopServices.openUrl(QUrl(url))
        self.status_label.setText(f"已打开地图页面: {map_name}")

    def _copy_text(self, text):
        QApplication.clipboard().setText(text)
        self.status_label.setText(
            f"已复制: {text[:40]}{'...' if len(text) > 40 else ''}"
        )

    # ========== 搜索 ==========
    def _on_search(self):
        kw = self.search_edit.text().strip().lower()
        if not kw:
            self.status_label.setText("请输入搜索内容")
            return
        self._search_keyword = kw
        self._search_matches = []
        self._search_index = -1
        self._search_tree_version = self._tree_version

        def walk(item):
            t0 = item.text(0).lower()
            t1 = item.text(1).lower()
            t5 = item.text(5).lower()
            if kw in t0 or kw in t1 or kw in t5:
                self._search_matches.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        self._goto_next_match()

    def _on_search_next(self):
        if not self._search_matches or self._search_tree_version != self._tree_version:
            self._on_search()
            return
        self._goto_next_match()

    def _goto_next_match(self):
        if not self._search_matches:
            self.status_label.setText(f"未找到包含 “{self._search_keyword}” 的节点")
            return
        self._search_index = (self._search_index + 1) % len(self._search_matches)
        item = self._search_matches[self._search_index]
        parent = item.parent()
        while parent:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item, QTreeWidget.PositionAtCenter)
        self.status_label.setText(
            f"匹配 {self._search_index + 1}/{len(self._search_matches)}: "
            f"{item.text(0).strip()}"
        )

    # ========== 延迟测量 ==========
    def _start_ping(self):
        if self._ping_running:
            if hasattr(self, "ping_worker") and self.ping_worker:
                self.ping_worker.cancel()
                self.status_label.setText("正在取消测量...")
            return
        addresses = []
        for srv in self.servers_data.get("servers", []):
            addrs = srv.get("addresses", [])
            if addrs:
                addresses.append(addrs[0])
        if not addresses:
            self.status_label.setText("没有可测量的服务器地址")
            return
        self._ping_running = True
        self.ping_btn.setText("取消测量")
        self.status_label.setText(f"测量 0/{len(addresses)} ...")
        self.ping_worker = PingWorker(addresses, timeout=1.2, workers=40)
        self.ping_worker.ping_result.connect(self._on_ping_result)
        self.ping_worker.progress.connect(self._on_ping_progress)
        self.ping_worker.finished_all.connect(self._on_ping_finished)
        self.ping_worker.start()

    def _on_ping_result(self, address, ms):
        self.server_ping[address] = ms

    def _on_ping_progress(self, done, total):
        self.status_label.setText(f"测量 {done}/{total} ...")

    def _on_ping_finished(self):
        self._ping_running = False
        self.ping_btn.setText("测延迟")
        self.status_label.setText("延迟测量完成")
        if self._sort_mode == "latency":
            self._refresh_server_tree()
        else:
            self._update_latency_column()

    def _update_latency_column(self):
        def walk(item):
            key = item.data(0, Qt.UserRole)
            if key and key.startswith("ADDR:"):
                addr = key[5:]
                ms = self.server_ping.get(addr, None)
                item.setText(
                    4, f"{ms:.0f} ms" if ms and ms > 0 else ("超时" if ms == -1 else "")
                )
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    # ========== 档案树 ==========
    def _refresh_player_tree(self):
        expanded = set()

        def save(it):
            if it.isExpanded():
                k = it.data(0, Qt.UserRole)
                if k:
                    expanded.add(k)
            for i in range(it.childCount()):
                save(it.child(i))

        for i in range(self.player_tree.topLevelItemCount()):
            save(self.player_tree.topLevelItem(i))

        self.player_tree.setUpdatesEnabled(False)
        self.player_tree.clear()
        self._ptree_items.clear()  # 新增，必须加，否则持有已销毁的 item

        pd = self.player_data
        if not pd:
            self._pending_scan.start()
            self.player_tree.setUpdatesEnabled(True)
            return

        types = pd.get("types") or {}
        total_finished = 0
        type_map_counts = {}
        for type_name, type_data in types.items():
            maps = type_data.get("maps", {}) or {}
            finished = {
                k: v
                for k, v in maps.items()
                if isinstance(v, dict) and v.get("finishes", 0) > 0
            }
            type_map_counts[type_name] = len(finished)
            total_finished += len(finished)

        overview = QTreeWidgetItem(["📊 总览", "", "", ""])
        overview.setData(0, Qt.UserRole, "overview")
        overview.setExpanded(True)
        points = pd.get("points", {})
        self._row(overview, "总排名", points.get("rank"))
        self._row(overview, "总分数", points.get("points"))
        self._row(overview, "可获得最大分数", points.get("total"))
        self._row(overview, "已完成地图总数", f"{total_finished} 张")
        self._row(
            overview, "常用服务器", pd.get("favorite_server", {}).get("server", "?")
        )
        ff = pd.get("first_finish", {})
        if ff:
            ts = datetime.fromtimestamp(ff.get("timestamp", 0)).strftime("%Y-%m-%d")
            self._row(
                overview,
                "首次完成",
                f"{unescape_map_name(ff.get('map', '?'))}  {ff.get('time', 0):.2f}s  ({ts})",
            )
        self.player_tree.addTopLevelItem(overview)

        tn = QTreeWidgetItem(["📅 时间维度", "", "", ""])
        tn.setData(0, Qt.UserRole, "time")
        tn.setExpanded(True)
        for label, key in [
            ("上周", "points_last_week"),
            ("上月", "points_last_month"),
            ("去年", "points_last_year"),
        ]:
            d = pd.get(key, {}) or {}
            tn.addChild(
                QTreeWidgetItem(
                    [
                        f"  {label}",
                        f"排名 {d.get('rank', '?')}",
                        f"分数 {d.get('points', '?')}",
                        "",
                    ]
                )
            )
        self.player_tree.addTopLevelItem(tn)

        for type_name, type_data in types.items():
            tp = type_data.get("points") or {}
            count = type_map_counts.get(type_name, 0)
            t = QTreeWidgetItem(
                [
                    f"🎮 {type_label(type_name)}",
                    f"排名 {tp.get('rank', '?')}",
                    f"分数 {tp.get('points', '?')} / 最大 {tp.get('total', '?')}",
                    f"已完成 {count} 张地图",
                ]
            )
            t.setData(0, Qt.UserRole, f"type:{type_name}")
            t.setToolTip(0, type_tooltip(type_name))  # 中文说明

            t.setToolTip(
                2,
                f"当前获得 {tp.get('points', '?')} 分 / 可获得最大 {tp.get('total', '?')} 分",
            )
            t.setToolTip(3, f"该类型下已完成 {count} 张地图")
            t.setExpanded(False)

            maps = type_data.get("maps", {}) or {}
            finished = {
                k: v
                for k, v in maps.items()
                if isinstance(v, dict) and v.get("finishes", 0) > 0
            }
            for map_key, md in sorted(
                finished.items(), key=lambda x: -x[1].get("points", 0)
            ):
                map_name = unescape_map_name(map_key)
                child = QTreeWidgetItem(
                    [
                        f"  {map_name}",
                        f"排名 {md.get('rank', '?')}",
                        f"分数 {md.get('points', '?')}",
                        f"{md.get('finishes', 0)} 次完成 · {md.get('time', 0):.2f}s",
                    ]
                )
                child.setData(0, Qt.UserRole, f"map:{map_name}")
                child.setToolTip(
                    0, map_tooltip(map_name, map_meta_memory.get(map_name))
                )
                # 不在这里挂图标：展开 + 滚入视口才处理
                t.addChild(child)
                self._ptree_items.setdefault(map_name, []).append(child)

            self.player_tree.addTopLevelItem(t)

        rc = QTreeWidgetItem(["🕒 最近完成", "", "", ""])
        rc.setData(0, Qt.UserRole, "recent")
        rc.setExpanded(False)
        for f in pd.get("last_finishes", []) or []:
            ts = datetime.fromtimestamp(f.get("timestamp", 0)).strftime("%m-%d %H:%M")
            rc.addChild(
                QTreeWidgetItem(
                    [
                        f"  {unescape_map_name(f.get('map', '?'))}",
                        f.get("type", ""),
                        f"{f.get('time', 0):.2f}s",
                        f"{f.get('country', '')}  {ts}",
                    ]
                )
            )
        self.player_tree.addTopLevelItem(rc)

        pt = QTreeWidgetItem(["🤝 常用伙伴", "", "", ""])
        pt.setData(0, Qt.UserRole, "partners")
        pt.setExpanded(False)
        for p in pd.get("favorite_partners", []) or []:
            pt.addChild(
                QTreeWidgetItem(
                    [f"  {p.get('name', '?')}", f"{p.get('finishes', 0)} 次", "", ""]
                )
            )
        self.player_tree.addTopLevelItem(pt)

        def restore(it):
            if it.data(0, Qt.UserRole) in expanded:
                it.setExpanded(True)
            for i in range(it.childCount()):
                restore(it.child(i))

        for i in range(self.player_tree.topLevelItemCount()):
            restore(self.player_tree.topLevelItem(i))
        self._enqueue_expanded_ptree_thumbs()
        self.player_tree.setUpdatesEnabled(True)

    def _on_player_tree_expanded(self, item):
        """展开类型节点时，把该组地图排到 PRIO_NEAR（比后台批量优先）"""
        for i in range(item.childCount()):
            k = item.child(i).data(0, Qt.UserRole) or ""
            if isinstance(k, str) and k.startswith("map:"):
                name = k[4:]
                if self._icon_from_cache(name) is None:
                    self._request_thumb(name, priority=PRIO_NEAR)
        self._pending_scan.start()

    def _enqueue_expanded_ptree_thumbs(self):
        """重建树后，已经处于展开状态的节点补一次入队"""

        def walk(it):
            if it.isExpanded():
                self._on_player_tree_expanded(it)
            for i in range(it.childCount()):
                walk(it.child(i))

        for i in range(self.player_tree.topLevelItemCount()):
            walk(self.player_tree.topLevelItem(i))

    @staticmethod
    def _row(parent, label, value):
        parent.addChild(QTreeWidgetItem([f"  {label}", str(value), "", ""]))

    # ========== 详情 ==========
    def _on_selection_changed(self):
        item = self.tree.currentItem()
        if not item:
            self.detail.clear()
            return
        payload = item.data(0, Qt.UserRole + 1)
        if not payload:
            self.detail.clear()
            return
        if payload.get("type") == "player":
            self._show_player_detail(payload)
        else:
            self._show_server_detail(payload)

    def _show_server_detail(self, payload):
        info = payload["info"]
        address = payload.get("address", "")
        mi = info.get("map", {})
        map_name = mi.get("name", "?") if isinstance(mi, dict) else "?"
        ms = self.server_ping.get(address, None)
        lat = f"{ms:.0f} ms" if ms and ms > 0 else ("超时" if ms == -1 else "未测量")
        fav_map = " ⭐ 已收藏" if map_name in self.fav_maps else ""

        lines = [
            f"服务器: {info.get('name', '?')}",
            f"地址: {address or '?'}",
            f"延迟: {lat}",
            f"类型: {info.get('game_type', '?')}",
            f"地图: {map_name}{fav_map}",
            f"版本: {info.get('version', '?')}",
            f"最大客户端: {info.get('max_clients', '?')}",
            f"最大玩家: {info.get('max_players', '?')}",
            f"需要登录: {info.get('requires_login', False)}",
            f"有密码: {info.get('passworded', False)}",
            "",
            "提示：右键可收藏地图、打开地图、复制信息",
            "",
            "玩家列表:",
        ]
        for c in info.get("clients", []):
            if isinstance(c, dict) and c.get("is_player"):
                mark = " ⭐" if c.get("name") in self.fav_players else ""
                lines.append(
                    f"  {c.get('name', '?')}{mark}  |  分数 {c.get('score', 0)}  |  Team {c.get('team', 0)}"
                )
        self.detail.setPlainText("\n".join(lines))

    def _show_player_detail(self, payload):
        cli = payload["data"]
        info = payload["server"]
        address = payload.get("address", "")
        ms = self.server_ping.get(address, None)
        lat = f"{ms:.0f} ms" if ms and ms > 0 else ("超时" if ms == -1 else "未测量")
        pname = cli.get("name", "?")
        fav = " ⭐ 已收藏" if pname in self.fav_players else ""

        lines = [
            f"玩家: {pname}{fav}",
            f"所在服务器: {info.get('name', '?')}",
            f"服务器地址: {address or '?'}",
            f"服务器延迟: {lat}",
            f"分数: {cli.get('score', 0)}",
            f"队伍: {cli.get('team', 0)}",
            f"国家: {cli.get('country', '?')}",
            f"AFK: {cli.get('afk', False)}",
            "",
            "提示：右键可收藏此玩家、查看档案、打开地图",
        ]
        self.detail.setPlainText("\n".join(lines))

    # ========== 地图元数据 / 缩略图回调 ==========
    def _on_meta_ready(self, map_name, meta):
        text = self._map_cell_text(map_name, meta)
        tip = map_tooltip(map_name, meta)
        non_ddnet = not (meta or {}).get("type")
        for item in self._map_items.get(map_name, []):
            try:
                item.setText(1, text)
                item.setToolTip(1, tip)
                if non_ddnet:
                    self._apply_non_ddnet_style(item)
            except RuntimeError:
                pass

    def _on_meta_failed(self, map_name):
        pass

    # ========== 可见区域懒加载 ==========
    def _init_loading_asset(self):
        if os.path.exists(LOADING_GIF):
            movie = QMovie(LOADING_GIF)
            if movie.isValid():
                movie.setCacheMode(QMovie.CacheAll)  # 先把帧都读进内存，动画更顺
                movie.jumpToFrame(0)  # ← PyQt5 用这个，不是 jumpToFirstFrame
                pix = movie.currentPixmap()
                if not pix.isNull():
                    self._loading_movie = movie
                    self._loading_pix = pix

        if self._loading_pix is None or self._loading_pix.isNull():
            self._loading_pix = self._make_placeholder("加载中…", "#2b2b2b")
        self._loading_icon = QIcon(self._loading_pix)
        self._broken_icon = QIcon(self._make_placeholder("无图", "#3a2020"))

    def _make_placeholder(self, text, bg):
        pm = QPixmap(160, 120)
        pm.fill(QColor(bg))
        p = QPainter(pm)
        p.setPen(QColor("#9a9a9a"))
        p.setFont(QFont("Microsoft YaHei", 10))
        p.drawText(pm.rect(), Qt.AlignCenter, text)
        p.end()
        return pm

    def _on_any_scroll(self, *args):
        self._pending_scan.start()

    def _iter_items(self, tree):
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            yield it
            for i in range(it.childCount()):
                stack.append(it.child(i))

    @staticmethod
    def _item_map_name(item):
        name = item.data(0, Qt.UserRole + 2) or ""
        if name:
            return name
        k = item.data(0, Qt.UserRole) or ""
        if isinstance(k, str) and k.startswith("map:"):
            return k[4:]
        return ""

    def _request_thumb(self, name, priority=PRIO_BULK):
        """同一条地图只发一次请求；但允许低→高优先级插队"""
        self._thumb_requested.add(name)
        self.crawler.enqueue_thumb(name, priority=priority)

    def _apply_visible_icons(self):
        self._visible_pending = []
        self._apply_visible_in(self.tree, 1)
        self._apply_visible_in(self.player_tree, 0)
        
    def _apply_visible_in(self, tree, col):
        if not tree.isVisible():
            return

        only_visible = bool(self.settings.get("visible_only", True))
        vp = tree.viewport().rect()

        for item in self._iter_items(tree):
            # ① 先取地图名（这行丢了就会报 UnboundLocalError）
            name = self._item_map_name(item)
            if not name:
                continue

            # ② 需要时判断是否在视口内
            if only_visible:
                rect = tree.visualItemRect(item)
                if not rect.isValid() or not rect.intersects(vp):
                    continue

            # ③ 有缓存直接用，没有就挂 loading 并排队
            icon = self._icon_from_cache(name)
            if icon is None:
                if name in self._broken_thumbs:
                    icon = self._broken_icon
                else:
                    icon = self._loading_icon
                    self._visible_pending.append((tree, item, col))
                    self._request_thumb(name, priority=PRIO_VISIBLE)

            try:
                if item.icon(col).cacheKey() != icon.cacheKey():
                    item.setIcon(col, icon)
            except RuntimeError:
                pass


    def _tick_loading(self):
        """只推进视口内 loading 行的动画帧"""
        if not self._visible_pending:
            return
        if self._loading_movie is not None:
            self._loading_movie.jumpToNextFrame()
            pm = self._loading_movie.currentPixmap()
            if not pm.isNull():
                self._loading_icon = QIcon(pm)
        alive = []
        for tree, item, col in self._visible_pending:
            try:
                name = self._item_map_name(item)
                if name in self._broken_thumbs:
                    item.setIcon(col, self._broken_icon)
                    continue
                item.setIcon(col, self._loading_icon)
                alive.append((tree, item, col))
            except RuntimeError:
                pass
        self._visible_pending = alive

    def _icon_from_cache(self, map_name):
        """本地有图就立刻返回 QIcon，不等爬虫队列"""
        icon = self._thumb_icons.get(map_name)
        if icon is not None:
            return icon
        p = thumb_path(map_name)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            icon = QIcon(p)  # 延迟解码：只解码可见行
            self._thumb_icons[map_name] = icon
            return icon
        return None

    def _on_thumb_ready(self, map_name, payload):
        if isinstance(payload, str):
            pix = QPixmap(payload)
        else:
            pix = QPixmap.fromImage(payload)
        if pix.isNull():
            # 缓存里的坏文件（0 字节 / 错误页）删掉，下次可重下
            if isinstance(payload, str):
                try:
                    os.remove(payload)
                except OSError:
                    pass
            self._broken_thumbs.add(map_name)
            self._thumb_requested.discard(map_name)
            self._pending_scan.start()
            return

        self._broken_thumbs.discard(map_name)

        self._thumb_icons[map_name] = QIcon(pix)
        self._thumb_cache[map_name] = pix
        # 不遍历全树 setIcon，只让可见区重刷一次
        self._pending_scan.start()

    def _on_thumb_failed(self, map_name):
        self._broken_thumbs.add(map_name)
        self._thumb_requested.discard(map_name)
        self._pending_scan.start()  # 关键：让可见区重算，换成“无图”

    def _on_crawler_queue(self, pending):
        if pending > 0:
            self.crawler_status_label.setText(f"🐢 爬取队列: {pending} 个任务")
        else:
            self.crawler_status_label.setText("✅ 爬取队列空闲")

    # ========== 双击地图列 ==========
    def _on_tree_double_clicked(self, item, column):
        if column != 1:
            return
        # 从 item 的数据里取地图名，不再依赖 widget
        payload = item.data(0, Qt.UserRole + 1)
        if not payload or payload.get("type") != "server":
            return
        info = payload.get("info", {})
        mi = info.get("map", {})
        map_name = mi.get("name", "") if isinstance(mi, dict) else ""
        if not map_name:
            return
        self._open_map(unescape_map_name(map_name))

    # ========== 关闭窗口 ==========
    def closeEvent(self, event):
        try:
            if hasattr(self, "crawler") and self.crawler.isRunning():
                self.crawler.stop()
                self.crawler.wait(5000)
        except Exception:
            pass
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    settings = load_settings(SETTINGS_FILE)
    if not (settings.get("player") or "").strip():
        dlg = SettingsDialog(settings)
        if dlg.exec_() != QDialog.Accepted:
            sys.exit(0)
        settings = dlg.values()
        save_settings(SETTINGS_FILE, settings)

    win = DDNetMonitor(settings)
    win.show()
    sys.exit(app.exec_())

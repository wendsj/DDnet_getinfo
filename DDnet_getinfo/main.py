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
)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt, QUrl
from PyQt5.QtGui import QColor, QFont, QDesktopServices

MASTER_URLS = [
    "https://master1.ddnet.org/ddnet/15/servers.json",
    "https://master2.ddnet.org/ddnet/15/servers.json",
    "https://master3.ddnet.org/ddnet/15/servers.json",
]
PLAYER_URL_TEMPLATE = "https://ddnet.org/players/?json2={}"
REFRESH_INTERVAL_MS = 10_000
TARGET_PLAYER = "adyy6"
DDNET_DEFAULT_PORT = 8303
MAP_WEB_BASE = "https://teeworlds.cn/ddnet/maps/"


def _get_fav_file():
    # 打包后用 exe 所在目录，源码运行用脚本目录
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    # 目录可写就用同目录（便携模式）
    if os.access(base, os.W_OK):
        return os.path.join(base, "favorites.json")

    # 否则回退到用户目录
    user_dir = os.path.join(os.path.expanduser("~"), ".ddnet_monitor")
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "favorites.json")


FAV_FILE = _get_fav_file()

ONLINE_COLOR = QColor("#2ecc71")
OFFLINE_COLOR = QColor("#e74c3c")

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


# ---------- 收藏持久化 ----------
def _load_favorites():
    if os.path.exists(FAV_FILE):
        try:
            with open(FAV_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("players", [])), set(data.get("maps", []))
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
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DDNet 实时监控 — {TARGET_PLAYER}")
        self.resize(1980, 950)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("准备就绪")
        self.status_bar.addWidget(self.status_label)

        # ---- 数据 / 状态 ----
        self.servers_data = {}
        self.player_data = {}
        self.current_player = TARGET_PLAYER
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
        self._online_players = {}  # name -> [(server_key, info), ...]
        self._map_servers = {}  # map_name -> [server_key, ...]

        # 搜索状态
        self._search_keyword = ""
        self._search_matches = []
        self._search_index = -1
        self._search_tree_version = -1
        self._tree_version = 0

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
        self.type_filter_combo.setMinimumWidth(110)
        self.type_filter_combo.currentIndexChanged.connect(self._on_type_filter_changed)
        tl.addWidget(self.type_filter_combo)

        tl.addWidget(QLabel("排序:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["按在线玩家", "按延迟", "按名称"])
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tl.addWidget(self.sort_combo)

        self.sort_order_btn = QPushButton("↓ 降序")
        self.sort_order_btn.clicked.connect(self._toggle_sort_order)
        tl.addWidget(self.sort_order_btn)

        self.ping_btn = QPushButton("测延迟")
        self.ping_btn.clicked.connect(self._start_ping)
        tl.addWidget(self.ping_btn)

        # ---- 三栏 ----
        main_splitter = QSplitter(Qt.Horizontal)

        # 左：服务器 / 玩家树
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["服务器 / 玩家", "地图 / Team", "人数", "分数", "延迟", "地址"]
        )
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 60)
        self.tree.setColumnWidth(3, 60)
        self.tree.setColumnWidth(4, 70)
        self.tree.setColumnWidth(5, 230)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        main_splitter.addWidget(self.tree)

        # 中：详情
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Consolas", 10))
        main_splitter.addWidget(self.detail)

        # 右：标签页容器
        player_panel = QWidget()
        pl = QVBoxLayout(player_panel)
        pl.setContentsMargins(0, 0, 0, 0)

        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(4, 4, 4, 4)

        self.player_title = QLabel(f"👤 {TARGET_PLAYER} — 完整档案")
        self.player_title.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        title_layout.addWidget(self.player_title, 1)

        self.restore_btn = QPushButton(f"↩ 恢复 {TARGET_PLAYER}")
        self.restore_btn.clicked.connect(lambda: self._switch_player(TARGET_PLAYER))
        self.restore_btn.setVisible(False)
        title_layout.addWidget(self.restore_btn)

        pl.addWidget(title_row)

        # TabWidget
        self.right_tabs = QTabWidget()
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        # Tab 0：玩家档案
        self.player_tree = QTreeWidget()
        self.player_tree.setHeaderLabels(
            ["名称 / 类别", "排名", "分数 / 最大总分", "已完成地图"]
        )
        self.player_tree.setColumnWidth(0, 190)
        self.player_tree.setColumnWidth(1, 110)
        self.player_tree.setColumnWidth(2, 150)
        self.player_tree.setColumnWidth(3, 130)
        self.player_tree.setFont(QFont("Consolas", 9))
        self.player_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.player_tree.customContextMenuRequested.connect(
            self._on_player_tree_context_menu
        )
        self.right_tabs.addTab(self.player_tree, "📊 档案")

        # Tab 1：收藏玩家
        self.fav_players_tree = QTreeWidget()
        self.fav_players_tree.setHeaderLabels(["玩家", "在线状态", "", ""])
        self.fav_players_tree.setColumnWidth(0, 220)
        self.fav_players_tree.setColumnWidth(1, 130)
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
        self.fav_maps_tree.setColumnWidth(0, 240)
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

        main_splitter.setSizes([700, 280, 760])

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(toolbar)
        cl.addWidget(main_splitter, 1)
        self.setCentralWidget(central)

        self._update_title()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.start_fetch)
        self.timer.start(REFRESH_INTERVAL_MS)

        self.start_fetch()

    # ========== 标题 / tab 切换 ==========
    def _on_right_tab_changed(self, idx):
        self._update_title()

    def _update_title(self):
        idx = self.right_tabs.currentIndex()
        if idx == 0:
            self.player_title.setText(f"👤 {self.current_player} — 完整档案")
            self.restore_btn.setVisible(self.current_player != TARGET_PLAYER)
        elif idx == 1:
            self.player_title.setText(f"⭐ 收藏玩家 ({len(self.fav_players)})")
            self.restore_btn.setVisible(False)
        else:
            self.player_title.setText(f"🗺 收藏地图 ({len(self.fav_maps)})")
            self.restore_btn.setVisible(False)

    # ========== 数据拉取 ==========
    def start_fetch(self):
        self.status_label.setText("正在刷新...")
        self.worker = FetchWorker(self.current_player)
        self.worker.data_ready.connect(self._on_data_ready)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.start()

    def _on_data_ready(self, servers, player, elapsed):
        if (
            player.get("player") != self.current_player
            and self.current_player != TARGET_PLAYER
        ):
            return
        self.servers_data = servers
        self.player_data = player
        self._update_type_filter_options()
        self._refresh_server_tree()
        self._build_online_index()
        self._refresh_player_tree()
        self._refresh_fav_players()
        self._refresh_fav_maps()

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

    # ========== 收藏切换 ==========
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

    # ========== 收藏面板刷新（增删改，不 clear 全量避免跳动） ==========
    def _refresh_fav_players(self):
        existing = {}
        for i in range(self.fav_players_tree.topLevelItemCount()):
            it = self.fav_players_tree.topLevelItem(i)
            n = it.data(0, Qt.UserRole)
            if n:
                existing[n] = it
        # 移除已取消收藏的
        for n, it in list(existing.items()):
            if n not in self.fav_players:
                idx = self.fav_players_tree.indexOfTopLevelItem(it)
                if idx >= 0:
                    self.fav_players_tree.takeTopLevelItem(idx)
                del existing[n]
        # 更新 / 新增
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
                    srv_names = [info.get("name", "?") for _, info in locs]
                    it.setToolTip(0, "\n".join(srv_names))
                else:
                    it.setToolTip(0, "")
            else:
                it = QTreeWidgetItem([text, status, "", ""])
                it.setForeground(0, color)
                it.setForeground(1, color)
                it.setData(0, Qt.UserRole, pname)
                if is_online:
                    srv_names = [info.get("name", "?") for _, info in locs]
                    it.setToolTip(0, "\n".join(srv_names))
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

    # ========== 跳转到位置 ==========
    def _goto_player_in_tree(self, pname):
        if not pname:
            return
        locs = self._online_players.get(pname, [])
        if not locs:
            self.status_label.setText(f"玩家 {pname} 当前不在线")
            return
        # 找到第一个在主树里可见的位置
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
        for gt in sorted(game_types):
            self.type_filter_combo.addItem(gt)
        idx = self.type_filter_combo.findText(current)
        self.type_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.type_filter_combo.blockSignals(False)

    def _on_type_filter_changed(self, index):
        if self.servers_data:
            self._refresh_server_tree()
            self._build_online_index()

    # ========== 排序 / 过滤 / key ==========
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
            servers = [
                s
                for s in servers
                if s.get("info", s).get("game_type", "") == filter_type
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
            return srv.get("info", srv).get("name", "").lower()

        if self._sort_mode == "players":
            servers.sort(key=player_count, reverse=self._sort_desc)
        elif self._sort_mode == "latency":
            servers.sort(key=latency, reverse=self._sort_desc)
        else:
            servers.sort(key=name_key, reverse=self._sort_desc)
        return servers

    def _on_sort_changed(self, index):
        modes = ["players", "latency", "name"]
        self._sort_mode = modes[index]
        self._sort_desc = self._sort_mode == "players"
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
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self._item_map.clear()
        filtered = self._sorted_servers()
        self._last_shown_count = len(filtered)
        for srv in filtered:
            self._add_server_node(srv)
        self._restore_tree_state()
        self.tree.setUpdatesEnabled(True)
        self._tree_version += 1

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
        item.setText(1, map_name)
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

        players.sort(key=lambda c: c.get("score", 0), reverse=True)
        for cli in players:
            self._add_player_node(item, cli, info, address, server_key)

    def _add_player_node(self, parent_item, cli, srv_info, address, server_key):
        pname = cli.get("name", "?")
        score = cli.get("score", 0)
        team = cli.get("team", 0)

        p_item = QTreeWidgetItem()
        p_item.setText(0, f"  {pname}")
        if team:
            p_item.setText(1, f"Team {team}")
        p_item.setText(3, str(score))

        if pname == TARGET_PLAYER:
            p_item.setForeground(0, QColor("#FF6B6B"))
            f = p_item.font(0)
            f.setBold(True)
            p_item.setFont(0, f)
        elif pname in self.fav_players:
            p_item.setForeground(0, QColor("#f39c12"))
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

    # ========== 主树右键 ==========
    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        payload = item.data(0, Qt.UserRole + 1)
        if not payload:
            return

        menu = QMenu(self)

        if payload.get("type") == "server":
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
        if not item:
            return
        key = item.data(0, Qt.UserRole) or ""
        menu = QMenu(self)

        if key.startswith("type:"):
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
        if online:
            act.setEnabled(True)
        else:
            act.setEnabled(False)
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
        url = MAP_WEB_BASE + quote(map_name, safe="")
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

        pd = self.player_data
        if not pd:
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
                f"{ff.get('map', '?')}  {ff.get('time', 0):.2f}s  ({ts})",
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
                    f"🎮 {type_name}",
                    f"排名 {tp.get('rank', '?')}",
                    f"分数 {tp.get('points', '?')} / 最大 {tp.get('total', '?')}",
                    f"已完成 {count} 张地图",
                ]
            )
            t.setData(0, Qt.UserRole, f"type:{type_name}")
            t.setToolTip(0, TYPE_DESCRIPTIONS.get(type_name, ""))
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
            for map_name, md in sorted(
                finished.items(), key=lambda x: -x[1].get("points", 0)
            ):
                t.addChild(
                    QTreeWidgetItem(
                        [
                            f"  {map_name}",
                            f"排名 {md.get('rank', '?')}",
                            f"分数 {md.get('points', '?')}",
                            f"{md.get('finishes', 0)} 次完成 · {md.get('time', 0):.2f}s",
                        ]
                    )
                )
            self.player_tree.addTopLevelItem(t)

        rc = QTreeWidgetItem(["🕒 最近完成", "", "", ""])
        rc.setData(0, Qt.UserRole, "recent")
        rc.setExpanded(False)
        for f in pd.get("last_finishes", []) or []:
            ts = datetime.fromtimestamp(f.get("timestamp", 0)).strftime("%m-%d %H:%M")
            rc.addChild(
                QTreeWidgetItem(
                    [
                        f"  {f.get('map', '?')}",
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

        self.player_tree.setUpdatesEnabled(True)

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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = DDNetMonitor()
    win.show()
    sys.exit(app.exec_())

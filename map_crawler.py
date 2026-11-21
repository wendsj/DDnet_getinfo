# map_crawler.py
import os
import queue
import threading
import time
from urllib.parse import quote

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from map_meta import map_meta_memory, save_map_meta_cache
from thumb_loader import thumb_path, escape_map_name

PRIO_VISIBLE = 0  # 当前视口可见
PRIO_NEAR = 1  # 当前展开区域（不在视口）
PRIO_BULK = 2  # 后台批量


class _Throttle:
    """全局令牌桶：控制对 ddnet.org 的总请求速率"""

    def __init__(self, min_gap=0.30):
        self.min_gap = max(0.05, min_gap)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_gap
        if delay > 0:
            time.sleep(min(delay, 2.0))


def _make_session():
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=8, pool_maxsize=8, max_retries=1
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "ddnet-monitor/1.0"})
    return s


class MapCrawler(QThread):
    meta_ready = pyqtSignal(str, dict)
    meta_failed = pyqtSignal(str)
    thumb_ready = pyqtSignal(str, str)  # map_name, local_path
    thumb_failed = pyqtSignal(str)
    queue_size = pyqtSignal(int)

    def __init__(self, min_interval=0.3, max_interval=0.3, workers=3):
        super().__init__()
        # 兼容旧调用：取平均值当最小间隔，真正限速交给 _Throttle
        self.min_gap = max(0.05, (min_interval + max_interval) / 2.0)
        self.workers = max(1, workers)

        self._queue = queue.PriorityQueue()
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = False
        self._throttle = _Throttle(self.min_gap)
        self._tls = threading.local()

        self._meta_state = {}  # name -> "queued:<prio>" / "done"
        self._thumb_state = {}  # name -> "queued:<prio>" / "done"
        self._last_emit = 0.0

    # ---------- 主线程调用 ----------
    def _put(self, prio, task, name):
        with self._lock:
            self._seq += 1
            seq = self._seq
        self._queue.put((prio, seq, task, name))
        self._emit_queue_size()

    def enqueue_meta(self, map_name, priority=PRIO_NEAR):
        with self._lock:
            st = self._meta_state.get(map_name)
            if st == "done":
                return False
            # 已经在排队且优先级更高或相同 → 不重复入队
            if isinstance(st, str) and st.startswith("queued:"):
                if int(st.split(":")[1]) <= priority:
                    return False
            self._meta_state[map_name] = f"queued:{priority}"
        self._put(priority, "meta", map_name)
        return True

    def enqueue_thumb(self, map_name, priority=PRIO_BULK):
        """
        priority 越小越急；同一地图被更高优先级请求时会插队，
        旧的队列项在出队时自动作废（见 _claim）。
        """
        with self._lock:
            st = self._thumb_state.get(map_name)
            if st == "done":
                return False
            if isinstance(st, str) and st.startswith("queued:"):
                if int(st.split(":")[1]) <= priority:
                    return False
            self._thumb_state[map_name] = f"queued:{priority}"
        self._put(priority, "thumb", map_name)
        return True

    def stop(self):
        self._stop = True
        # 插入与 worker 数量相同的终止哨兵，且优先级最高
        for _ in range(self.workers):
            self._put(PRIO_VISIBLE - 1, "__stop__", None)

    def pending_count(self):
        return self._queue.qsize()

    def _emit_queue_size(self):
        now = time.monotonic()
        qsize = self._queue.qsize()
        if qsize == 0 or (now - self._last_emit) >= 0.4:
            self._last_emit = now
            self.queue_size.emit(qsize)

    # ---------- 工作线程 ----------
    def _session(self):
        s = getattr(self._tls, "s", None)
        if s is None:
            s = _make_session()
            self._tls.s = s
        return s

    def run(self):
        threads = [
            threading.Thread(target=self._worker, daemon=True)
            for _ in range(self.workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _worker(self):
        while not self._stop:
            try:
                prio, _seq, task, name = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if task == "__stop__":
                break
            try:
                if task == "meta":
                    if self._claim("meta", name, prio):
                        self._do_meta(name)
                else:
                    if self._claim("thumb", name, prio):
                        self._do_thumb(name)
            finally:
                self._emit_queue_size()

    def _claim(self, kind, name, prio):
        """这条队列项是否仍是最新优先级？被插队升级过的旧项直接丢弃。"""
        state = self._meta_state if kind == "meta" else self._thumb_state
        with self._lock:
            if state.get(name) != f"queued:{prio}":
                return False
            state[name] = "done"
        return True

    def _do_meta(self, name):
        url = f"https://ddnet.org/maps/?json={quote(name, safe='')}"
        self._throttle.wait()
        try:
            resp = self._session().get(url, timeout=10)
            if resp.status_code != 200:
                self.meta_failed.emit(name)
                return
            data = resp.json()
            meta = {
                "type": data.get("type"),
                "points": data.get("points"),
                "difficulty": data.get("difficulty"),
                "thumbnail": data.get("thumbnail"),  # 官方缩略图地址，最可靠
            }
            map_meta_memory[name] = meta
            save_map_meta_cache()
            self.meta_ready.emit(name, meta)
        except Exception:
            self.meta_failed.emit(name)

    @staticmethod
    def _thumb_urls(name):
        safe = "!$&'()*+,;=:@-_.~[]{}"
        urls = []

        # 1) 官方 JSON 里直接给的 thumbnail（最可靠）
        meta = map_meta_memory.get(name) or {}
        thumb = meta.get("thumbnail")
        if thumb:
            urls.append(thumb)

        # 2) 兜底：非字母数字 → _
        fname = escape_map_name(name)
        urls.append(f"https://ddnet.org/ranks/maps/{quote(fname, safe=safe)}.png")

        # 3) 再兜底：原始名（空格变 %20）
        urls.append(f"https://ddnet.org/ranks/maps/{quote(name, safe=safe)}.png")

        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _do_thumb(self, name):
        local = thumb_path(name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            self.thumb_ready.emit(name, local)
            return

        for url in self._thumb_urls(name):
            self._throttle.wait()
            try:
                resp = self._session().get(url, timeout=10)
            except Exception:
                continue
            if resp.status_code != 200 or not resp.content:
                continue
            data = resp.content
            # 必须是 PNG，避免把 404/HTML 错误页写进缓存
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                continue
            tmp = local + ".part"
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, local)
            except OSError:
                continue
            self.thumb_ready.emit(name, local)
            return

        self.thumb_failed.emit(name)

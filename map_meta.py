# map_meta.py
import os
import sys
import json
import requests
from urllib.parse import quote
from PyQt5.QtCore import QThread, pyqtSignal


def _get_app_dir():
    """打包后用 exe 所在目录，源码运行用脚本所在目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_cache_dir():
    """缓存根目录：优先 exe/脚本同目录，不可写时回退到用户目录"""
    base = _get_app_dir()
    cache = os.path.join(base, "cache")
    try:
        os.makedirs(cache, exist_ok=True)
        # 用写临时文件测试是否可写，避免只读权限导致后续写缓存时崩溃
        test = os.path.join(cache, ".write_test")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return cache
    except Exception:
        # 兜底：Program Files 等只读目录
        fallback = os.path.join(os.path.expanduser("~"), ".ddnet_monitor")
        os.makedirs(fallback, exist_ok=True)
        return fallback


CACHE_DIR = _get_cache_dir()
MAP_META_CACHE_FILE = os.path.join(CACHE_DIR, "map_meta.json")

# 内存缓存：{map_name: {"type": str, "points": int, "difficulty": int}}
map_meta_memory = {}


def load_map_meta_cache():
    if os.path.exists(MAP_META_CACHE_FILE):
        try:
            with open(MAP_META_CACHE_FILE, "r", encoding="utf-8") as f:
                map_meta_memory.update(json.load(f))
        except Exception:
            pass


def save_map_meta_cache():
    try:
        with open(MAP_META_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(map_meta_memory, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class MapMetaFetcher(QThread):
    meta_ready = pyqtSignal(str, dict)
    meta_failed = pyqtSignal(str)

    def __init__(self, map_name):
        super().__init__()
        self.map_name = map_name

    def run(self):
        try:
            url = f"https://ddnet.org/maps/?json={quote(self.map_name, safe='')}"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                self.meta_failed.emit(self.map_name)
                return
            data = resp.json()
            meta = {
                "type": data.get("type"),
                "points": data.get("points"),
                "difficulty": data.get("difficulty"),
            }
            self.meta_ready.emit(self.map_name, meta)
        except Exception:
            self.meta_failed.emit(self.map_name)
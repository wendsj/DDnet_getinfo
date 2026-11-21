# thumb_loader.py
import os
import re

from map_meta import CACHE_DIR

THUMB_DIR = os.path.join(CACHE_DIR, "thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)


def escape_map_name(name):
    if not name:
        return ""
    return "".join(ch if (ch.isascii() and ch.isalnum()) else "_" for ch in name)


def unescape_map_name(name):
    """ddnet.org 文件名 → 显示名：-33- -> !，-45- -> -
    只对 ddnet.org 返回的数据使用；服务器列表里的名字本身就是原始名。
    """
    if not name:
        return name
    return re.sub(r"-(\d{1,3})-", lambda m: chr(int(m.group(1))), name)


def thumb_path(map_name):
    """本地缩略图缓存路径（文件名用 DDNet 转义规则）"""
    safe = escape_map_name(map_name)
    return os.path.join(THUMB_DIR, f"{safe}.png")

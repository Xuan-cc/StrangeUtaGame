"""字体本地化名称解析。

``QFontDatabase.families()`` 在非对应语言系统上通常只返回字体的英文/罗马名
（如日文教科书体返回 ``HGKyokashotai`` 而非「HG教科書体」），导致无法用母语名搜索。

本模块直接读取系统字体文件的 OpenType ``name`` 表，提取各语言的字体族名，
构建「英文族名 → {语言ID: 本地化名}」映射，供字体选择器显示与搜索。

纯标准库实现（``struct`` + 文件 IO），对解析失败的文件静默跳过；仅 Windows 下
扫描字体目录，其它平台返回空映射（此时退化为仅用 Qt 提供的族名）。
"""

from __future__ import annotations

import glob
import os
import struct
import sys
from functools import lru_cache

# OpenType name 表 nameID
_NAME_FAMILY = 1          # Font Family name
_NAME_PREF_FAMILY = 16    # Typographic/Preferred Family

# platformID
_PLAT_MAC = 1
_PLAT_WIN = 3

# Windows 英文 languageID（用于推断与 Qt families() 对齐的英文族名）
_ENGLISH_LANGS = {0x0409, 0x0809, 0x0C09, 0x1009}


def _decode_name(platform: int, encoding: int, data: bytes) -> str | None:
    try:
        if platform == _PLAT_WIN:
            return data.decode("utf-16-be")
        if platform == _PLAT_MAC and encoding == 0:
            return data.decode("mac-roman")
        return data.decode("utf-16-be")
    except Exception:
        return None


def _font_offsets(f) -> list[int]:
    """返回字体内各子字体的 sfnt 偏移（普通字体 [0]，TTC 集合多个）。"""
    f.seek(0)
    tag = f.read(4)
    if tag == b"ttcf":
        f.seek(8)
        num = struct.unpack(">I", f.read(4))[0]
        if num <= 0 or num > 1000:
            return [0]
        return list(struct.unpack(">%dI" % num, f.read(4 * num)))
    return [0]


def _find_name_table(f, sfnt_offset: int) -> int | None:
    f.seek(sfnt_offset)
    head = f.read(6)
    if len(head) < 6:
        return None
    _ver, num_tables = struct.unpack(">IH", head)
    if num_tables <= 0 or num_tables > 1000:
        return None
    f.seek(sfnt_offset + 12)
    dir_data = f.read(16 * num_tables)
    for i in range(num_tables):
        entry = dir_data[i * 16 : i * 16 + 16]
        if len(entry) < 16:
            break
        tag, _checksum, offset, _length = struct.unpack(">4sIII", entry)
        if tag == b"name":
            return offset
    return None


def _parse_name_table(f, table_offset: int) -> list[tuple[int, int, int, int, str]]:
    """返回 (platformID, encodingID, languageID, nameID, string) 列表（仅族名）。"""
    f.seek(table_offset)
    header = f.read(6)
    if len(header) < 6:
        return []
    _fmt, count, string_offset = struct.unpack(">HHH", header)
    if count <= 0 or count > 5000:
        return []
    records_raw = f.read(12 * count)
    storage = table_offset + string_offset
    out: list[tuple[int, int, int, int, str]] = []
    for i in range(count):
        rec = records_raw[i * 12 : i * 12 + 12]
        if len(rec) < 12:
            break
        pid, eid, lid, nid, length, offset = struct.unpack(">HHHHHH", rec)
        if nid not in (_NAME_FAMILY, _NAME_PREF_FAMILY):
            continue
        f.seek(storage + offset)
        s = _decode_name(pid, eid, f.read(length))
        if s:
            out.append((pid, eid, lid, nid, s.strip()))
    return out


_FONT_EXTS = (".ttf", ".ttc", ".otf", ".otc")
_FONTS_REG_SUBKEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"


def _system_fonts_dir() -> str:
    return os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _font_dirs() -> list[str]:
    dirs = [_system_fonts_dir()]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return [d for d in dirs if os.path.isdir(d)]


def _registry_font_files() -> list[str]:
    """从注册表读取已登记字体的文件路径（权威来源，含非标准安装位置）。"""
    try:
        import winreg
    except Exception:
        return []
    fonts_dir = _system_fonts_dir()
    files: list[str] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, _FONTS_REG_SUBKEY)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    _name, value, _type = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if not isinstance(value, str) or not value:
                    continue
                # 注册表数据可能是多文件（逗号分隔）或单文件，且可能为相对名
                for part in value.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    path = part if os.path.isabs(part) else os.path.join(fonts_dir, part)
                    if os.path.splitext(path)[1].lower() in _FONT_EXTS:
                        files.append(path)
        finally:
            winreg.CloseKey(key)
    return files


def _font_files() -> list[str]:
    """收集所有字体文件路径：目录 glob ∪ 注册表登记，去重后返回存在的文件。"""
    candidates: list[str] = []
    for d in _font_dirs():
        for ext in _FONT_EXTS:
            candidates.extend(glob.glob(os.path.join(d, "*" + ext)))
    candidates.extend(_registry_font_files())

    seen: set[str] = set()
    files: list[str] = []
    for p in candidates:
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(p):
            files.append(p)
    return files


@lru_cache(maxsize=1)
def localized_alias_map() -> dict[str, dict[int, str]]:
    """构建 ``{英文族名: {languageID: 本地化族名}}`` 映射。

    仅 Windows 下扫描；解析失败的文件跳过。结果缓存（首次调用约 1~2 秒）。
    """
    if not sys.platform.startswith("win"):
        return {}
    result: dict[str, dict[int, str]] = {}
    for path in _font_files():
        try:
            with open(path, "rb") as f:
                offsets = _font_offsets(f)
                for off in offsets:
                    nt = _find_name_table(f, off)
                    if nt is None:
                        continue
                    records = _parse_name_table(f, nt)
                    _merge_records(result, records)
        except Exception:
            continue
    return result


def _merge_records(
    result: dict[str, dict[int, str]],
    records: list[tuple[int, int, int, int, str]],
) -> None:
    """从单个子字体的 name 记录中提取英文族名与各语言本地化名并并入 result。"""
    # 英文族名候选：Windows 英文（优先 nameID 16），其次 Mac 英文(lang 0)
    english: str | None = None
    eng_pref = None  # nameID 16 优先
    for pid, eid, lid, nid, s in records:
        if pid == _PLAT_WIN and lid in _ENGLISH_LANGS:
            if nid == _NAME_PREF_FAMILY:
                eng_pref = s
            elif english is None:
                english = s
    english = eng_pref or english
    if english is None:
        for pid, eid, lid, nid, s in records:
            if pid == _PLAT_MAC and lid == 0:
                english = s
                break
    if not english:
        return

    # 本地化名：非英文语言的 Windows 名（nameID 16 优先于 1）
    natives: dict[int, str] = {}
    for pid, eid, lid, nid, s in records:
        if pid != _PLAT_WIN or lid in _ENGLISH_LANGS:
            continue
        if lid not in natives or nid == _NAME_PREF_FAMILY:
            natives[lid] = s
    if not natives:
        return
    # 同一英文名可能来自多个子字体（不同字重），合并语言条目
    bucket = result.setdefault(english, {})
    for lid, s in natives.items():
        bucket.setdefault(lid, s)

"""应用设置数据层 — AppSettings 及 RL 字典解析函数。

从 settings_interface.py 拆出，保留公共 API 不变：
- ``AppSettings``：应用设置管理（config.json + dictionary.json + singers.json）
- ``_parse_rl_dictionary``：RL 字典文本解析（模块内私有，被 AppSettings 和 DictionaryEditDialog 复用）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
import json
import sys


class AppSettings:
    """应用设置管理"""

    DEFAULT_SETTINGS = {
        "audio": {
            "default_volume": 80,
            "default_speed": 1.0,
            "auto_play_on_load": False,
        },
        "timing": {
            "default_check_count": 1,
            "auto_advance_after_tag": True,
            "show_preview_lines": 5,
            "tag_offset_ms": -130,
            "speed_correction": 100,
            "fast_forward_ms": 5000,
            "rewind_ms": 5000,
            "jump_before_ms": 3000,
            "timing_adjust_step_ms": 10,
        },
        "auto_check": {
            "hiragana": True,
            "katakana": True,
            "kanji": True,
            "alphabet": False,
            "digit": False,
            "symbol": False,
            "space": False,
            "auto_on_load": True,
            "check_n": False,
            "check_sokuon": False,
            "check_parentheses": True,
            "check_empty_lines": False,
            "check_line_start": False,
            "check_line_end": True,
            "space_after_japanese": True,
            "space_after_alphabet": True,
            "space_after_symbol": True,
            "small_kana": False,
            "check_space_as_line_end": True,
            "checkpoint_on_punctuation": False,
        },
        "ui": {
            "theme": "light",
            "language": "zh_CN",
            "font_size": 24,
        },
        "export": {
            "default_format": "LRC (增强型)",
            "auto_add_extension": True,
            "last_export_dir": "",
            "offset_ms": -100,
        },
        "ruby_dictionary": {
            "enabled": True,
        },
        "nicokara_tags": {
            "title": "",
            "artist": "",
            "album": "",
            "tagging_by": "",
            "silence_ms": 0,
            "custom": [],
        },
        "auto_save": {
            "enabled": True,
            "interval_minutes": 5,
        },
        "shortcuts": {
            # 打轴模式：音乐播放时生效（以实时打轴操作为主）
            # 注：以 _SHORTCUT_ACTIONS 为唯一真源，此处需保持一致
            "timing_mode": {
                "play_pause": "A",
                "stop": "S",
                "tag_now": "Space",
                "seek_back": "Z",
                "seek_forward": "X",
                "speed_down": "Q",
                "speed_up": "W",
                "edit_ruby": "F2",
                "add_checkpoint": "F5",
                "remove_checkpoint": "F6",
                "volume_up": "",
                "volume_down": "",
                "nav_prev_line": "UP",
                "nav_next_line": "DOWN",
                "nav_prev_char": "LEFT",
                "nav_next_char": "RIGHT",
                "cycle_checkpoint_prev": "ALT+LEFT",
                "toggle_line_end": "F4",
                "toggle_word_join": "F3",
                "timestamp_up": "ALT+UP",
                "timestamp_down": "ALT+DOWN",
                "cycle_checkpoint": "ALT+RIGHT",
                "break_line_here": "Return",
                "delete_char": "Delete",
                "delete_timestamp":"BackSpace",
            },
            # 编辑模式：音乐暂停/停止时生效（以歌词/注音编辑为主）
            "edit_mode": {
                "play_pause": "A",
                "stop": "S",
                "tag_now": "Space",
                "seek_back": "Z",
                "seek_forward": "X",
                "speed_down": "Q",
                "speed_up": "W",
                "edit_ruby": "F2",
                "add_checkpoint": "Space",
                "remove_checkpoint": "Backspace",
                "volume_up": "",
                "volume_down": "",
                "nav_prev_line": "UP",
                "nav_next_line": "DOWN",
                "nav_prev_char": "LEFT",
                "nav_next_char": "RIGHT",
                "cycle_checkpoint_prev": "ALT+LEFT",
                "toggle_line_end": ".",
                "toggle_word_join": "F3",
                "timestamp_up": "ALT+UP",
                "timestamp_down": "ALT+DOWN",
                "cycle_checkpoint": "ALT+RIGHT",
                "break_line_here": "Return",
                "delete_char": "Delete",
                "delete_timestamp":"",
            },
        },
    }

    @staticmethod
    def get_config_dir() -> Path:
        """获取配置文件目录（默认为程序所在目录）。

        支持通过程序目录下的 .config_redirect 文件重定向到自定义位置。
        """
        program_dir = Path(sys.argv[0]).resolve().parent
        redirect_file = program_dir / ".config_redirect"
        if redirect_file.exists():
            try:
                custom_dir = Path(redirect_file.read_text(encoding="utf-8").strip())
                if custom_dir.is_dir():
                    return custom_dir
            except Exception:
                pass
        return program_dir

    @staticmethod
    def _get_packaged_config_path(filename: str) -> Optional[Path]:
        """获取内嵌配置文件路径（兼容开发环境和 PyInstaller 打包环境）。"""
        # PyInstaller 打包后
        base = getattr(sys, "_MEIPASS", None)
        if base:
            p = Path(base) / "strange_uta_game" / "config" / filename
            if p.exists():
                return p
        # 开发环境：相对于本文件的位置（settings/ → frontend/ → strange_uta_game/）
        dev_path = Path(__file__).resolve().parent.parent.parent / "config" / filename
        if dev_path.exists():
            return dev_path
        return None

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_dir = self.get_config_dir()
            try:
                config_dir.mkdir(exist_ok=True)
            except OSError:
                # 程序目录不可写时回退到用户目录
                config_dir = Path.home() / ".strange_uta_game"
                config_dir.mkdir(exist_ok=True)
            self._config_path = config_dir / "config.json"
            # 迁移：若新位置无配置，检查旧位置
            if not self._config_path.exists():
                old_config = Path.home() / ".strange_uta_game" / "config.json"
                if old_config.exists():
                    try:
                        import shutil

                        shutil.copy2(str(old_config), str(self._config_path))
                    except Exception:
                        pass
        else:
            self._config_path = Path(config_path)

        self._dict_path = self._config_path.parent / "dictionary.json"
        self._singers_path = self._config_path.parent / "singers.json"
        self._settings = self._load_settings()
        self._migrate_to_separate_files()
        self._ensure_default_dictionary()

    def _ensure_default_dictionary(self) -> None:
        """首次启动时，将内置 RL 字典固化为默认 dictionary.json。"""
        if self._dict_path.exists():
            return
        # 优先使用打包的 dictionary.json
        packaged = self._get_packaged_config_path("dictionary.json")
        if packaged:
            try:
                import shutil

                shutil.copy2(str(packaged), str(self._dict_path))
                return
            except Exception:
                pass
        # 回退到代码内置的默认字典文本
        try:
            from strange_uta_game.backend.infrastructure.data.default_dictionary import (
                DEFAULT_RL_DICT_TEXT,
            )

            entries = _parse_rl_dictionary(DEFAULT_RL_DICT_TEXT)
            if entries:
                self._save_json(self._dict_path, entries)
        except Exception as e:
            print(f"初始化默认词典失败: {e}")

    def _load_settings(self) -> Dict[str, Any]:
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    settings = self._deep_copy_defaults()
                    self._deep_merge(settings, loaded)
                    return settings
            except Exception as e:
                print(f"加载设置失败: {e}")

        # 回退到内嵌默认配置
        packaged = self._get_packaged_config_path("config.json")
        if packaged:
            try:
                with open(packaged, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    settings = self._deep_copy_defaults()
                    self._deep_merge(settings, loaded)
                    return settings
            except Exception:
                pass

        return self._deep_copy_defaults()

    def _deep_copy_defaults(self) -> Dict[str, Any]:
        """递归深拷贝默认设置"""
        return json.loads(json.dumps(self.DEFAULT_SETTINGS))

    def _deep_merge(self, base: Dict, override: Dict) -> None:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def save(self) -> None:
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存设置失败: {e}")

    def reload(self) -> None:
        """从磁盘重新加载配置文件。"""
        self._settings = self._load_settings()

    def get(self, path: str, default=None) -> Any:
        keys = path.split(".")
        value = self._settings
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, path: str, value: Any) -> None:
        keys = path.split(".")
        target = self._settings
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value

    def get_all(self) -> Dict[str, Any]:
        return self._settings.copy()

    # ── 独立文件：词典 & 演唱者预设 ──

    def _migrate_to_separate_files(self) -> None:
        """将旧 config.json 中的 ruby_dictionary.entries 和 singer_presets 迁移到独立文件。"""
        dirty = False
        # 迁移词典条目
        dict_data = self._settings.get("ruby_dictionary", {})
        if isinstance(dict_data, dict) and "entries" in dict_data:
            entries = dict_data.pop("entries", [])
            if entries and not self._dict_path.exists():
                self._save_json(self._dict_path, entries)
            dirty = True
        # 迁移演唱者预设
        if "singer_presets" in self._settings:
            presets = self._settings.pop("singer_presets", [])
            if presets and not self._singers_path.exists():
                self._save_json(self._singers_path, presets)
            dirty = True
        if dirty:
            self.save()

    @staticmethod
    def _save_json(path: Path, data: Any) -> None:
        """写入 JSON 文件。"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存文件失败 {path}: {e}")

    @staticmethod
    def _load_json(path: Path, default: Any = None) -> Any:
        """读取 JSON 文件，不存在时回退到内嵌默认文件。"""
        if not path.exists():
            # 尝试从内嵌配置包回退
            packaged = AppSettings._get_packaged_config_path(path.name)
            if packaged:
                try:
                    with open(packaged, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return default if default is not None else []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"加载文件失败 {path}: {e}")
            return default if default is not None else []

    def load_dictionary(self) -> list:
        """从 dictionary.json 加载用户词典条目。"""
        return self._load_json(self._dict_path, [])

    def save_dictionary(self, entries: list) -> None:
        """保存用户词典条目到 dictionary.json。"""
        self._save_json(self._dict_path, entries)

    def register_dictionary_word(self, word: str, reading: str) -> None:
        """新增或更新单个词条：如已存在则删除旧条目，将新条目置顶（最高优先级）。"""
        word = (word or "").strip()
        reading = (reading or "").strip()
        if not word:
            return
        entries = self.load_dictionary()
        entries = [e for e in entries if (e.get("word") or "").strip() != word]
        entries.insert(0, {"enabled": True, "word": word, "reading": reading})
        self.save_dictionary(entries)

    def import_rl_dictionary(self, text: str) -> tuple:
        """导入 RL 字典文本：逆序遍历，重复条目以新导入覆盖并置顶。

        Returns:
            (added, updated): 新增数量与覆盖数量。
        """
        new_entries = _parse_rl_dictionary(text)
        if not new_entries:
            return (0, 0)
        entries = self.load_dictionary()
        index = {(e.get("word") or "").strip(): i for i, e in enumerate(entries)}
        added = 0
        updated = 0
        # 逆序遍历：使原文件顺序靠前的词条最终置顶
        for entry in reversed(new_entries):
            word = (entry.get("word") or "").strip()
            if not word:
                continue
            if word in index:
                # 覆盖旧条目：删除后插入顶部
                old_idx = index[word]
                entries.pop(old_idx)
                updated += 1
            else:
                added += 1
            entries.insert(0, {
                "enabled": True,
                "word": word,
                "reading": entry.get("reading", ""),
            })
            # 重建索引（位置已变）
            index = {(e.get("word") or "").strip(): i for i, e in enumerate(entries)}
        self.save_dictionary(entries)
        return (added, updated)

    def load_singer_presets(self) -> list:
        """从 singers.json 加载演唱者预设。"""
        return self._load_json(self._singers_path, [])

    def save_singer_presets(self, presets: list) -> None:
        """保存演唱者预设到 singers.json。"""
        self._save_json(self._singers_path, presets)


def _parse_rl_dictionary(text: str) -> list:
    """解析 RL 字典文件格式（薄包装，实现位于后端
    :mod:`strange_uta_game.backend.infrastructure.parsers.rl_dictionary`）。

    保留此函数以兼容 ``dictionary_dialog`` / ``settings_interface`` 的历史导入路径。
    """
    from strange_uta_game.backend.infrastructure.parsers.rl_dictionary import (
        parse_rl_dictionary,
    )

    return parse_rl_dictionary(text)

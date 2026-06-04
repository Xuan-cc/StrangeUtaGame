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
            "speed_slider_min": 0.2,
            "speed_slider_max": 1.0,
            "auto_play_on_load": False,
            # 高质量音频变速：开启用离线 TSM 预渲染（变速不变调、无爆音，占用 .cache
            # 磁盘缓存）；关闭仅用原版 BASS 实时变速（零缓存，但可能爆音）。
            "hq_speed_change": True,
        },
        "timing": {
            "default_check_count": 1,
            "auto_advance_after_tag": True,
            "show_preview_lines": 5,
            "tag_offset_ms": -230,
            "speed_correction": 100,
            "fast_forward_ms": 5000,
            "rewind_ms": 5000,
            "jump_before_ms": 3000,
            "timing_adjust_step_ms": 10,
            "disable_click_jump": False,
            "keysound_enabled": False,
            "keysound_volume": 100,
            "keysound_style": "default",
            "guide_symbol": "",
            "guide_count": 1,
            "guide_duration_ms": 1000,
            "scroll_mode": "auto",  # auto / always / never
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
            "check_n": True,
            "check_sokuon": True,
            "check_long_vowel": False,
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
            "check_english_word_end": True,
            "chinese_lyrics_detection": True,
            "romanize_ruby": False,
            "delete_ruby_types": [],
        },
        "ui": {
            "theme": "auto",
            "language": "zh_CN",
            # 窗口习惯：启动时读取以恢复上次的窗口大小与最大化状态；
            # 用户改变窗口大小或最大化时实时写回（见 MainWindow）。
            "window_size": [1400, 900],
            "window_maximized": False,
            "font_size": 24,
            "main_font": "Microsoft YaHei",
            "ruby_font": "Microsoft YaHei",
            "lyrics_alignment": "left",
            "alignment_margin": 168,
            "checkpoint_markers": {
                "cp_first_timed": "▶",
                "cp_first_empty": "▷",
                "cp_multi_timed": "▮",
                "cp_multi_empty": "▯",
                "cp_sentence_end_timed": "⬟",
                "cp_sentence_end_empty": "⬠",
            },
        },
        "export": {
            "default_format": "Nicokara (带注音)",
            "auto_add_extension": True,
            "last_export_dir": "",
            "offset_ms": 0,
            "software_compensation_ms": 0,
            "nicokara_pause_char": "^",
        },
        "ruby_split_mode": "mora",  # 注音分段方式: "direct", "char", "mora"
        "ruby_dictionary": {
            "enabled": True,
            "annotate_katakana_with_english": False,
        },
        "llm_ruby": {
            "enabled": False,
            "provider": "openai",      # "openai" | "anthropic" | "custom"
            "base_url": "",
            "api_key": "",
            "model": "",
            "apply_user_dict": True,    # LLM 注音后是否仍应用用户词典
            "timeout_sec": 60,
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
        "tools": {
            "ffmpeg_path": "",
        },
        "complete_timestamp": {
            "scope_types": [
                "kanji",
                "hiragana",
                "katakana",
                "sokuon",
                "long_vowel",
                "chon",
                "chisai_kana",
            ],
            "exclude_rules": [
                "linked",
            ],
        },
        "shortcuts": {
            # 打轴模式：音乐播放时生效（以实时打轴操作为主）
            # 注：以 _SHORTCUT_ACTIONS 为唯一真源，此处需保持一致
            # 格式: "key:trigger" trigger 为 short 或 long；空字符串表示未绑定
            "timing_mode": {
                "play_pause": "D:short",
                "stop": "S:short",
                "tag_now": "Space:short",
                "tag_now_extra": "",
                "tag_and_delete_next": "",
                "seek_back": "Z:short",
                "seek_forward": "X:short",
                "speed_down": "Q:short",
                "speed_up": "W:short",
                "edit_ruby": "F2:short",
                "add_checkpoint": "F5:short",
                "remove_checkpoint": "F6:short",
                "volume_up": "",
                "volume_down": "",
                "nav_prev_line": "UP:short",
                "nav_next_line": "DOWN:short",
                "nav_prev_char": "LEFT:short",
                "nav_next_char": "RIGHT:short",
                "cycle_checkpoint_prev": "ALT+LEFT:short",
                "toggle_line_end": "F4:short",
                "toggle_word_join": "F3:short",
                "timestamp_up": "ALT+UP:short",
                "timestamp_down": "ALT+DOWN:short",
                "cycle_checkpoint": "ALT+RIGHT:short",
                "break_line_here": "Return:short",
                "delete_char": "Delete:short",
                "delete_timestamp": "BackSpace:short",
                "bulk_change": "CTRL+H:short",
                "modify_char": "",
                "insert_guide": "",
                "modify_line": "",
                "analyze_rubies": "",
                "delete_rubies_by_type": "",
                "set_singer_by_line": "",
                "apply_singer": "",
                "clear_timestamp": "",
                "timestamps_to_sentence_end": "",
                "clear_all_checkpoints": "",
                "quick_export": "",
                "insert_space": "M:short",
            },
            # 编辑模式：音乐暂停/停止时生效（以歌词/注音编辑为主）
            "edit_mode": {
                "play_pause": "D:short",
                "stop": "S:short",
                "tag_now": "Space:short",
                "tag_now_extra": "",
                "seek_back": "Z:short",
                "seek_forward": "X:short",
                "speed_down": "Q:short",
                "speed_up": "W:short",
                "edit_ruby": "F2:short",
                "add_checkpoint": "Space:short",
                "remove_checkpoint": "Backspace:short",
                "volume_up": "",
                "volume_down": "",
                "nav_prev_line": "UP:short",
                "nav_next_line": "DOWN:short",
                "nav_prev_char": "LEFT:short",
                "nav_next_char": "RIGHT:short",
                "cycle_checkpoint_prev": "ALT+LEFT:short",
                "toggle_line_end": ".:short",
                "toggle_word_join": "F3:short",
                "timestamp_up": "ALT+UP:short",
                "timestamp_down": "ALT+DOWN:short",
                "cycle_checkpoint": "ALT+RIGHT:short",
                "break_line_here": "Return:short",
                "delete_char": "Delete:short",
                "delete_timestamp": "",
                "bulk_change": "CTRL+H:short",
                "modify_char": "",
                "insert_guide": "",
                "modify_line": "",
                "analyze_rubies": "",
                "delete_rubies_by_type": "",
                "set_singer_by_line": "",
                "apply_singer": "",
                "clear_timestamp": "",
                "timestamps_to_sentence_end": "",
                "clear_all_checkpoints": "",
                "tag_now_editor": "",
                "tag_now_extra_editor": "",
                "quick_export": "",
                "insert_space": "M:short",
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
            # 如果用户配置不存在，从内嵌配置复制
            if not self._config_path.exists():
                self._copy_packaged_config()
        else:
            self._config_path = Path(config_path)

        self._dict_path = self._config_path.parent / "dictionary.json"
        self._network_dict_path = self._config_path.parent / "network_dictionary.json"
        self._singers_path = self._config_path.parent / "singers.json"
        self._settings = self._load_settings()
        self._migrate_to_separate_files()
        self._ensure_default_dictionary()
        self._force_upgrade_dictionary_if_needed()

    def _force_upgrade_dictionary_if_needed(self) -> None:
        """词典版本升级：比较内置词典版本号与用户已应用版本号。

        内置 config.json 含 ``dictionary_version``（整数），用户 config.json 含
        ``applied_dictionary_version``（整数，首次无此字段视为 0）。
        若用户版本 < 内置版本，按以下规则合并词典：
        - word 相同但 reading 不同：替换为内置版本
        - 用户词典中没有的 word：添加
        - 用户词典中有但内置没有的：保留（用户自定义）

        好处：之后只需递增内置 config.json 的 ``dictionary_version``，
        所有用户下次启动即自动升级，无需改代码。
        """
        # 读内置版本号
        packaged_cfg_path = self._get_packaged_config_path("config.json")
        packaged_version = 0
        if packaged_cfg_path:
            try:
                with open(packaged_cfg_path, "r", encoding="utf-8") as f:
                    packaged_cfg = json.load(f)
                packaged_version = int(packaged_cfg.get("dictionary_version", 0))
            except Exception:
                packaged_version = 0

        if packaged_version <= 0:
            return  # 内置没有版本号，跳过

        # 读用户已应用版本号（直接从用户 config.json 原始文件读，避免 merge 干扰）
        user_version = 0
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                user_version = int(user_cfg.get("applied_dictionary_version", 0))
            except Exception:
                user_version = 0

        if user_version >= packaged_version:
            return  # 已是最新版本

        # 加载内置词典和用户词典
        packaged_dict_path = self._get_packaged_config_path("dictionary.json")
        if not packaged_dict_path:
            return
        try:
            with open(packaged_dict_path, "r", encoding="utf-8") as f:
                packaged_entries = json.load(f)
        except Exception as e:
            print(f"Failed to read packaged dictionary: {e}")
            return

        # 加载用户词典（如果存在）
        user_entries = []
        if self._dict_path.exists():
            try:
                user_entries = self.load_dictionary()
            except Exception:
                user_entries = []

        # 构建用户词典索引：word -> (index, entry)
        user_index = {}
        for i, entry in enumerate(user_entries):
            word = (entry.get("word") or "").strip()
            if word:
                user_index[word] = (i, entry)

        # 合并逻辑：
        # 1. 遍历内置词典，替换 reading 不同的条目，添加新条目
        updated = 0
        added = 0
        for packaged_entry in packaged_entries:
            packaged_word = (packaged_entry.get("word") or "").strip()
            packaged_reading = (packaged_entry.get("reading") or "").strip()
            if not packaged_word:
                continue

            if packaged_word in user_index:
                # word 存在，检查 reading 是否相同
                idx, user_entry = user_index[packaged_word]
                user_reading = (user_entry.get("reading") or "").strip()
                if user_reading != packaged_reading:
                    # reading 不同，替换
                    user_entries[idx] = packaged_entry
                    updated += 1
            else:
                # word 不存在，添加到词典末尾
                user_entries.append(packaged_entry)
                added += 1

        # 保存更新后的用户词典
        try:
            self.save_dictionary(user_entries)
        except Exception as e:
            print(f"Failed to save merged dictionary: {e}")
            return

        # 写入已应用版本号到用户 config.json
        self._settings["applied_dictionary_version"] = packaged_version
        # 清理旧标志位（兼容旧版）
        self._settings.pop("is_dictionary_real_sugdic", None)
        try:
            self._save_json(self._config_path, self._settings)
        except Exception as e:
            print(f"Failed to write applied_dictionary_version: {e}")

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

    def _copy_packaged_config(self) -> None:
        """从内嵌配置文件复制到用户目录。"""
        packaged = self._get_packaged_config_path("config.json")
        if packaged:
            try:
                import shutil
                shutil.copy2(str(packaged), str(self._config_path))
            except Exception as e:
                print(f"复制内嵌配置失败: {e}")

    def _load_settings(self) -> Dict[str, Any]:
        # 从内嵌 config.json 加载默认值
        defaults = self._load_packaged_defaults()

        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # 以默认值为基础，用户配置覆盖
                    self._deep_merge(defaults, loaded)
            except Exception as e:
                print(f"加载设置失败: {e}")
        else:
            # 用户配置不存在，使用内嵌默认配置
            packaged = self._get_packaged_config_path("config.json")
            if packaged:
                try:
                    with open(packaged, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        self._deep_merge(defaults, loaded)
                except Exception:
                    pass

        return defaults

    def _load_packaged_defaults(self) -> Dict[str, Any]:
        """从内嵌 config.json 加载默认配置"""
        packaged = self._get_packaged_config_path("config.json")
        if packaged:
            try:
                with open(packaged, "r", encoding="utf-8") as f:
                    return json.load(f)
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
        """新增单个词条到词典顶部（最高优先级）。

        允许同 word 多条共存：完全相同 (word, reading) 才去重，避免重复点击产生
        无意义副本；其他读音变体并存，由 lookup 顺序（自顶向下首个命中）决定应用。
        """
        word = (word or "").strip()
        reading = (reading or "").strip()
        if not word:
            return
        entries = self.load_dictionary()
        new_entry = {"enabled": True, "word": word, "reading": reading}
        # 仅当存在完全一致 (word, reading) 时跳过添加；不再清除其他读音变体
        for e in entries:
            if (e.get("word") or "").strip() == word and (e.get("reading") or "").strip() == reading:
                return
        entries.insert(0, new_entry)
        self.save_dictionary(entries)

    def import_rl_dictionary(self, text: str) -> tuple:
        """导入 RL 字典文本：新条目整体插入到顶部，保持原文件顺序。

        允许同 word 多条共存：仅当 (word, reading) 与已有条目完全一致时跳过（去重），
        其他读音变体并存。lookup 时由顺序（自顶向下首个命中）决定优先级。

        Returns:
            (added, skipped): 新增数量与因完全重复被跳过的数量。
        """
        new_entries = _parse_rl_dictionary(text)
        if not new_entries:
            return (0, 0)
        entries = self.load_dictionary()
        existing_keys = {
            ((e.get("word") or "").strip(), (e.get("reading") or "").strip())
            for e in entries
        }
        to_prepend: list = []
        added = 0
        skipped = 0
        for entry in new_entries:
            word = (entry.get("word") or "").strip()
            reading = (entry.get("reading") or "").strip()
            if not word:
                continue
            key = (word, reading)
            if key in existing_keys:
                skipped += 1
                continue
            existing_keys.add(key)
            to_prepend.append({"enabled": True, "word": word, "reading": reading})
            added += 1
        # 整批插入到顶部，保留 new_entries 原顺序（首条最优先）
        entries = to_prepend + entries
        self.save_dictionary(entries)
        return (added, skipped)

    # ──────────────────────────────────────────────
    # 网络词典：meta 存 config.json[network_dictionary]，
    #          cache（entries + last_fetched）存 network_dictionary.json
    # ──────────────────────────────────────────────

    def load_network_dictionary(self) -> dict:
        """加载统一形态的网络词典文档（合并 meta + cache）。

        meta（启用/源列表/源排序/URL/名称等设置）从 ``config.json[network_dictionary]``
        读取；cache（每源 entries / last_fetched）从 ``network_dictionary.json`` 读取。
        缺失任一文件用 :data:`DEFAULT_NETWORK_DICTIONARY_META` 兜底；自动补齐内置源。
        旧版（一体式 ``network_dictionary.json``）会被自动迁移：拆分后写回。
        """
        from strange_uta_game.backend.infrastructure.network_dictionary import (
            DEFAULT_NETWORK_DICTIONARY_META,
            ensure_builtin_sources,
            merge_meta_and_cache,
        )

        meta = self.get("network_dictionary", None)
        if not isinstance(meta, dict):
            meta = json.loads(json.dumps(DEFAULT_NETWORK_DICTIONARY_META))

        cache: dict = {}
        if self._network_dict_path.exists():
            try:
                with open(self._network_dict_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                raw = None
            # 兼容旧一体式文档：``{"enabled","sources":[{...,"entries":[...]}],
            # "source_order":[...]}`` —— 拆出 meta 部分覆盖到 config.json，cache 留下。
            if isinstance(raw, dict) and "sources" in raw and isinstance(raw["sources"], list):
                if raw["sources"] and "entries" in raw["sources"][0]:
                    # 旧形态：拆分 + 迁移
                    from strange_uta_game.backend.infrastructure.network_dictionary import (
                        split_meta_and_cache,
                    )
                    migrated_meta, cache = split_meta_and_cache(raw)
                    self.set("network_dictionary", migrated_meta)
                    self._save_json(self._network_dict_path, cache)
                    meta = migrated_meta
                else:
                    cache = raw
            elif isinstance(raw, dict):
                cache = raw

        doc = merge_meta_and_cache(meta, cache)
        return ensure_builtin_sources(doc)

    def save_network_dictionary(self, doc: dict) -> None:
        """保存统一文档：meta → ``config.json``，cache → ``network_dictionary.json``。"""
        from strange_uta_game.backend.infrastructure.network_dictionary import (
            split_meta_and_cache,
        )
        meta, cache = split_meta_and_cache(doc)
        self.set("network_dictionary", meta)
        self.save()  # 立即落盘 meta 到 config.json
        self._save_json(self._network_dict_path, cache)

    def maybe_auto_update_network_dictionary(self, force: bool = False) -> "tuple[list, list, bool]":
        """检查并按需自动拉取所有启用的网络源。

        触发条件：``network_dictionary.auto_update.enabled=True`` 且距离
        ``last_auto_update_at`` 已超过 ``(interval_value, interval_unit)``。
        ``force=True`` 时无视条件强制执行（"立即同步"按钮可用）。

        非阻塞建议：调用方在后台线程中调用本方法（HTTP 慢，UI 线程不可阻塞）。

        Args:
            force: 是否无视开关与间隔强制拉取。

        Returns:
            ``(ok_msgs, fail_msgs, ran)``：成功/失败消息列表 + 是否真的执行了拉取。
            未到期 / 未启用 / ``enabled=False`` → ``ran=False`` 且 msgs 为空。
        """
        from strange_uta_game.backend.infrastructure.network_dictionary import (
            auto_update_enabled_sources,
            is_auto_update_due,
        )

        au_enabled = bool(self.get("network_dictionary.auto_update.enabled", False))
        if not force and not au_enabled:
            return ([], [], False)

        interval_value = int(self.get("network_dictionary.auto_update.interval_value", 1) or 1)
        interval_unit = str(self.get("network_dictionary.auto_update.interval_unit", "week") or "week")
        last_at = float(self.get("network_dictionary.last_auto_update_at", 0) or 0)
        if not force and not is_auto_update_due(last_at, interval_value, interval_unit):
            return ([], [], False)

        # 网络词典总开关未启用时，自动更新无意义（用户也看不到结果）—— 仍允许拉取以保持
        # entries 最新；但若希望节流可以这里返回。当前选择"拉取"，因为下次启用立即生效。
        doc = self.load_network_dictionary()
        ok_msgs, fail_msgs = auto_update_enabled_sources(doc)
        self.save_network_dictionary(doc)

        import time as _time
        self.set("network_dictionary.last_auto_update_at", int(_time.time()))
        self.save()
        return (ok_msgs, fail_msgs, True)

    def load_effective_dictionary(self) -> list:
        """加载用于注音 lookup 的完整词典：本地 + 启用的网络源，按全局优先级拼接。

        ``DictionaryEditDialog`` 等编辑场景仍使用 :meth:`load_dictionary`
        （仅本地）；只读消费者（``analyze_sentence`` 等）应改调本方法。
        """
        from strange_uta_game.backend.infrastructure.network_dictionary import (
            flatten_effective_dictionary,
        )
        local = self.load_dictionary()
        net = self.load_network_dictionary()
        return flatten_effective_dictionary(local, net)

    # ──────────────────────────────────────────────
    # LLM 注音
    # ──────────────────────────────────────────────

    def llm_ruby_active(self) -> bool:
        """LLM 注音是否处于激活态（已启用且连接信息齐全）。"""
        from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
            LLMRubyConfig,
        )

        return LLMRubyConfig.from_settings(self).enabled and LLMRubyConfig.from_settings(
            self
        ).is_complete()

    def llm_apply_user_dict(self) -> bool:
        """LLM 注音时是否仍应用用户词典（默认 True）。"""
        return bool(self.get("llm_ruby.apply_user_dict", True))

    def build_ruby_analyzer(
        self, lines: Optional[list] = None, annotate_katakana_with_english: bool = False
    ):
        """构建注音分析器：LLM 激活时返回 LLMRubyAnalyzer，否则走本地回退链。

        Args:
            lines: 整首歌词的行文本列表（LLM 整首一次发送所需）。LLM 未激活时忽略。
            annotate_katakana_with_english: 开启时让 LLM 为英语外来语片假名返回英文读音
                （无法对应英文的片假名放弃标注）。LLM 未激活时忽略。
        """
        from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
            create_analyzer,
        )

        if not self.llm_ruby_active():
            return create_analyzer()

        from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
            LLMRubyAnalyzer,
            LLMRubyConfig,
            _resolve_proxies,
        )

        cfg = LLMRubyConfig.from_settings(self)
        return LLMRubyAnalyzer(
            cfg,
            list(lines or []),
            fallback=create_analyzer(),
            proxies=_resolve_proxies(self),
            annotate_katakana_with_english=annotate_katakana_with_english,
        )

    def load_singer_presets(self) -> list:
        """从 singers.json 加载演唱者预设。"""
        return self._load_json(self._singers_path, [])

    def save_singer_presets(self, presets: list) -> None:
        """保存演唱者预设到 singers.json。"""
        self._save_json(self._singers_path, presets)


def build_annotated_reading(
    word: str,
    per_char_ruby: list,
    per_char_linked: "list[bool] | None" = None,
) -> str:
    """把 ``(word, per_char_ruby[, per_char_linked])`` 序列化为 annotated 行内格式。

    Args:
        word: 原文词（N 个字符）。
        per_char_ruby: 长度为 N 的列表，每项是对应字符的 ``Ruby`` 对象或 ``None``。
            ``Ruby.parts`` 中每个 ``RubyPart.text`` 对应一个 mora，用 ``|`` 连接写入。
        per_char_linked: 长度为 N 的布尔列表，``True`` 表示该字符与下一字符连词。
            若为 ``None``，则视所有字符独立（不连词）。

    完全尊重用户输入：不做自注音剥离、不做额外 mora 拆分、不做任何读音变换。

    连词处理：
        ``linked[i]=True`` 表示字符 ``i`` 与 ``i+1`` 在同一连词块内。
        连词链 ``[i..j]`` 合并为单个 annotated 块
        ``{word[i:j+1]||seg_i,seg_{i+1},...,seg_j}``，
        每段 ``seg_k`` 为该字符各 ``RubyPart.text`` 以 ``|`` 连接；无 ruby 的字符用空段。

    例：
        word="大冒険", ruby=[R("だ","い"), R("ぼ","う"), R("け","ん")], linked=[True,True,False]
        → ``{大冒険||だ|い,ぼ|う,け|ん}``

        word="食べ物", ruby=[R("た"), None, R("も","の")], linked=[False,False,False]
        → ``{食||た}べ{物||も|の}``
    """

    def _seg(ruby: "object | None") -> str:
        """把一个字符的 Ruby 对象序列化为段内字符串（mora 用 | 连接）。"""
        if ruby is not None and hasattr(ruby, "parts") and ruby.parts:
            return "|".join(p.text for p in ruby.parts if p.text)
        return ""

    n = len(word)
    linked: "list[bool]" = list(per_char_linked) if per_char_linked else [False] * n

    out: list = []
    i = 0
    while i < n:
        # 找到从 i 开始的连词链末尾 j（linked[i..j-1] 均为 True，linked[j] 为 False 或越界）
        j = i
        while j < n - 1 and j < len(linked) and linked[j]:
            j += 1
        # 链覆盖 [i..j]
        chain_chars = word[i : j + 1]
        chain_rubies = [
            per_char_ruby[k] if k < len(per_char_ruby) else None
            for k in range(i, j + 1)
        ]
        chain_segs = [_seg(r) for r in chain_rubies]

        if j > i:
            # 连词块：合并为 {chars||seg_i,seg_{i+1},...,seg_j}
            segs_str = ",".join(chain_segs)
            out.append(f"{{{chain_chars}||{segs_str}}}")
        else:
            # 单字
            seg = chain_segs[0]
            if seg:
                out.append(f"{{{chain_chars}||{seg}}}")
            else:
                out.append(chain_chars)
        i = j + 1

    return "".join(out)


def _parse_rl_dictionary(text: str) -> list:
    """解析 RL 字典文件格式（薄包装，实现位于后端
    :mod:`strange_uta_game.backend.infrastructure.parsers.rl_dictionary`）。

    保留此函数以兼容 ``dictionary_dialog`` / ``settings_interface`` 的历史导入路径。
    """
    from strange_uta_game.backend.infrastructure.parsers.rl_dictionary import (
        parse_rl_dictionary,
    )

    return parse_rl_dictionary(text)

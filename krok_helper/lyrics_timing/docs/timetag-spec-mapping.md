# Nicokara / LRC 타임태그 SHINTA 2025 規格 ↔ StrangeUtaGame 实装对照

> 参考规格：SHINTA「タイムタグ規格書（再整理版）」2025 改訂  
> 本仓库对应版本：Stage A 落地后（差异表 A / G / H / I / K 全部对齐）  
> 范围：`NicokaraParser` / `_apply_ruby_entries` / `nicokara_exporter` / `lyric_loader` / `AppSettings.nicokara_tags`

---

## 等级说明

| 等级 | 含义 |
|---|---|
| **必须** | 不实现会导致与 RhythmicaLyrics / SHINTA 工具链不兼容，必须落地 |
| **推荐** | 不实现可工作，但跨工具协作有风险，本仓库选择落地 |
| **可选** | 边缘特性，Stage A 不实装 |
| **✓ 已合规** | 既有实装与规格一致，无需改动 |

---

## 差异表

| ID | 规格条款 | 既有行为 | 实装状态 | 等级 | 备注 |
|---|---|---|---|---|---|
| **A** | 时间戳 `[MM:SS:CC]` 每段必须 2 位 | 接受 `\d{1,2}:\d{2}:\d{2}`（含 1 位分钟段） | **已落地（宽松+warning）** | 必须 | `NICOKARA_TS_PATTERN` 保留宽松匹配；新增 `NICOKARA_TS_STRICT_PATTERN`；`parse()` 入口对违规第 1 段计数 + `logger.warning` |
| B | 时间戳上限 `[99:59:99]` = 5,999,990ms | 不校验上限 | 未实装 | 可选 | Stage A 不实装；溢出由下游导出器自然 wrap |
| C | 拡張 ts `zz` 单位 = 10ms (厘秒, cs) | 已按 cs 解析 | ✓ 已合规 | 推荐 | `LRCParser` / `NicokaraParser` 均 `int(cc) * 10` |
| D | `@Offset` 单位 = 毫秒 (ms) | 由 `Project.offset_ms` 承载 | ✓ 已合规 | 推荐 | `lyric_loader._sync_nicokara_metadata_to_settings` 跳过 `@Offset`，避免双重写入 |
| E | `@Title` / `@Artist` / `@Album` | 已支持读写 | ✓ 已合规 | ✓ | `known_map` 映射到 `tags["title/artist/album"]` |
| F | `@TaggingBy` | 已支持读写 | ✓ 已合规 | ✓ | `tags["tagging_by"]` |
| **G** | @Ruby 适用区间 `[t1, t2]` 左闭右闭（`pos_start ≤ t ≤ pos_end`） | 末端使用 `>=`（左闭右开） | **已修正** | 必须 | `lyric_parser.py:1027` `>=` → `>`；docstring 同步 |
| **H** | `@RubyN` 编号从 1 连号递增、不跳号、不重复 | 不校验 | **已落地（宽松+warning）** | 推荐 | `ruby_indices` 收集 + return 前对比 `range(1, N+1)`，违规 `logger.warning` |
| **I** | ルビ留空 `@RubyN=漢字,,...` ⇒ 取消区间内 ruby；同 kanji 多 entry 后到覆盖先到 | 旧逻辑：跳过已存在 ruby + 顺序分配（第 N 条 → 第 N 次出现） | **已重写** | 必须 | `reading==""` ⇒ `set_ruby(None)` + `linked_to_next=False`；非空 entry 总是覆盖 `_distribute_reading_to_chars`；移除 `has_existing` 跳过 |
| J | ルビ可内嵌相对 ts（如 `お[00:00:30]し`） | 已支持 | ✓ 已合规 | ✓ | `_parse_reading_with_timestamps` |
| **K** | 未知 `@Foo=Bar` 标签需保留并 round-trip 回写 | 解析后丢弃 | **已落地** | 必须 | `lyric_loader._sync_nicokara_metadata_to_settings` 写入 `AppSettings.nicokara_tags`，已知键映射 + 其余 push `tags["custom"]`，每次导入覆盖式替换 |
| L | `@Emoji` 演唱者图标定义 | 解析为 `singer_definitions`，但 emoji 文件路径未落盘 | 部分实装 | 可选 | Stage A 不实装文件复制；singer key/name 映射已可用 |
| M | 行内 `【svN】` 切换演唱者 | 已支持 | ✓ 已合规 | ✓ | `_parse_body_line` 内 singer 切换 |
| N | 行尾释放 ts（双 ts 模式） | 已支持 | ✓ 已合规 | ✓ | `release_ts_map` / `line_end_ts` |

---

## 落地点索引

### A + H（解析诊断）

- 文件：[`lyric_parser.py`](../src/strange_uta_game/backend/infrastructure/parsers/lyric_parser.py)
- 关键位置：
  - 顶部 `import logging` + `logger = logging.getLogger(__name__)`
  - `NICOKARA_TS_STRICT_PATTERN = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")`（L507）
  - `parse()` 入口 ts 宽松违规计数 + warning（L551-562）
  - body-loop 内 `ruby_indices.append(...)`（L579）
  - return 前 `ruby_indices != expected` 校验 + warning（L626-636）
- 策略：**宽松 + warning**。解析继续，不阻断。等 Stage C 再引入 strict 开关。

### G（左闭右闭区间）

- 文件：[`lyric_parser.py`](../src/strange_uta_game/backend/infrastructure/parsers/lyric_parser.py)
- 关键位置：`_apply_ruby_entries` L1027 `char_ms > pos_end_ms`（旧 `>=`）
- docstring（L992-994）同步注明「左闭右闭，符合 SHINTA 2025『適用開始時刻 ≤ t ≤ 適用終了時刻』」

### I（ルビ留空清除 + 后到覆盖）

- 文件：[`lyric_parser.py`](../src/strange_uta_game/backend/infrastructure/parsers/lyric_parser.py)
- 关键位置：`_apply_ruby_entries` L1031-1052
  - `reading == ""` 分支：循环区间 `[pos, actual_end)` 调 `set_ruby(None)` 且重置 `linked_to_next=False`，防止历史连字残留
  - 非空 reading：移除 `has_existing` 跳过逻辑，总是调 `_distribute_reading_to_chars`，依赖循环顺序（`ruby_entries` 按文件出现顺序追加）天然实现 N 大者覆盖 N 小者
  - `linked_to_next` 重判：`range(pos, actual_end - 1)` 内逐对判定，仅在「同 tag 内 + 后字 body 无独立 ts」时 link

### K（AppSettings round-trip）

- 文件：[`lyric_loader.py`](../src/strange_uta_game/frontend/editor/timing/lyric_loader.py)
- 关键位置：
  - `_sync_nicokara_metadata_to_settings(metadata)`（L58-116）
  - Nicokara 分支末尾调用，传入 `NicokaraParseResult.metadata`
- 映射规则：
  - `Title / Artist / Album / TaggingBy` → `tags["title/artist/album/tagging_by"]`
  - `SilencemSec` → `tags["silence_ms"] (int)`，转换失败 fallback 到 custom
  - `Offset` → 跳过（由 `Project.offset_ms` 承载）
  - 其他全部 → `tags["custom"]` list，元素形如 `"@Key=Value"`
- 覆盖式：每次导入完全替换 `AppSettings.nicokara_tags`（用户语义：「每次写入项目都换」）
- 写入失败静默吞掉（`except Exception: pass`），不阻断导入；exporter 侧 fallback 仍能用旧值

---

## 不在 Stage A 范围

- **B**：ts 上限 `[99:59:99]` 校验
- **L 完整版**：`@Emoji` 文件名 → 实际 emoji 资源落盘
- **跨工具 round-trip 测试**（Stage B 占位，等用户提供合规 LRC 样本）
- **strict 模式开关**（Stage C 占位，把现在的 warning 提升为 ParseError）

---

## 验证

- `lsp_diagnostics`：`lyric_parser.py` / `lyric_loader.py` 双 clean
- `pytest tests/unit/infrastructure/test_lyric_parser.py tests/unit/infrastructure/test_exporters.py`：37 / 37 passed
- 附加 SHINTA 规格单测见 `TestApplyRubyEntries` / `TestNicokaraParserSpecCompliance` / `TestNicokaraTagsRoundTrip`

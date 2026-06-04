"""一键本地发布脚本。

子命令：

* ``prepare X.Y.Z``        修改 ``__version__.py`` 并往 ``CHANGELOG.md`` 顶部
                           插入新版本占位段落（如果还没有）。不动 Git。
* ``extract-notes X.Y.Z``  抽取 CHANGELOG.md 中对应版本的 Markdown 段落，
                           写到 stdout 或 ``--output`` 指定的文件 —— 用于
                           粘到 GitHub Release body。
* ``build``                跑 Updater 与主程序打包，生成
                           ``dist/StrangeUtaGame-vX.Y.Z.zip`` 与
                           ``dist/release_notes-vX.Y.Z.md``。需要先 prepare。
                           --variant 指定变体（main/noWinIME/mac，默认 main）。
* ``all X.Y.Z``            prepare → build → 提示后续 git/GitHub 步骤。

用法示例：

.. code-block:: bat

    python scripts\\release.py prepare 0.3.3
    # 此处手动编辑 CHANGELOG.md 把占位段落填上具体内容
    python scripts\\release.py build
    # 检查 dist/StrangeUtaGame-v0.3.3.zip
    git add -A & git commit -m "release v0.3.3" & git tag SUGv0.3.3 & git push --tags
    # 最后到 GitHub Web 创建 Release：选 SUGv0.3.3，上传 zip，粘 release_notes

多变体示例：

.. code-block:: bat

    python scripts\\release.py build --variant main
    python scripts\\release.py build --variant noWinIME
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time as _time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _force_utf8_stdio() -> None:
    """强制 stdout/stderr 使用 UTF-8。Windows 默认 cp1252/cp936 都会令中文 print 抛错。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "strange_uta_game" / "__version__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
UPDATER_BUILD = ROOT / "updater_app" / "build_updater.py"
UPDATER_EXE = ROOT / "updater_app" / "dist" / "Updater.exe"
MAIN_BUILD = ROOT / "build.py"
MAIN_DIST = ROOT / "dist" / "StrangeUtaGame"
RELEASE_DIST = ROOT / "dist"
# 记录上次成功打包的 runtime 内容哈希，随 git 提交，供 --reuse-runtime 使用。
RUNTIME_HASH_CACHE = ROOT / "scripts" / ".runtime-hash-cache.json"
# 稳定名称的 runtime zip 备份，不随版本号变化，供下次 build 复用。
RUNTIME_LATEST_ZIP = ROOT / "dist" / "runtime-latest.zip"
# 只哈希这个文件来判断 runtime 是否需要重建（版本锁定文件，不含开发工具的部分）。
REQUIREMENTS_FILE = ROOT / "requirements.txt"
# 不参与 pip freeze hash 计算的包名前缀列表（纯开发工具，不会进入打包产物）。
# 每行一个前缀（不区分大小写），以 # 开头的行视为注释。随 git 提交。
RUNTIME_FREEZE_EXCLUDE = ROOT / "scripts" / ".runtime-freeze-exclude.txt"

# 内置默认排除项（若排除文件不存在则使用此列表；文件存在则完全以文件为准）
_DEFAULT_FREEZE_EXCLUDES = [
    "pip",
    "setuptools",
    "wheel",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "build",
    "twine",
    "pytest",
    "pytest-cov",
    "coverage",
    "black",
    "ruff",
    "flake8",
    "mypy",
    "isort",
    "pylint",
    "pre-commit",
    "nox",
]

VERSION_RE = re.compile(r'^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$')


# ───────────────────────── VariantConfig ─────────────────────────


@dataclass
class VariantConfig:
    """一个发布变体的全部路径与命名约定。

    变体标识（variant）：
      - ``""`` / ``"main"``   Windows + WinRT（默认，已有用户群）
      - ``"noWinIME"``        Windows，无 WinRT，内置 sudachi 注音
      - ``"mac"``             macOS，无 WinRT，内置 sudachi 注音

    各变体资产命名规则（以 v1.0.3 为例）：
      main:      StrangeUtaGame-v1.0.3.zip / manifest-v1.0.3.json
      noWinIME:  StrangeUtaGame-noWinIME-v1.0.3.zip / manifest-noWinIME-v1.0.3.json
      mac:       StrangeUtaGame-mac-v1.0.3.zip / manifest-mac-v1.0.3.json
    """

    # 规范化的变体标识（""  表示 main）
    variant: str
    # PyInstaller 输出目录名与 EXE 文件名前缀
    app_name: str
    # dist/<app_name>/
    dist_dir: Path
    # scripts/.runtime-hash-cache[-variant].json
    runtime_hash_cache: Path
    # dist/runtime-latest[-variant].zip
    runtime_latest_zip: Path
    # 是否包含 Updater.exe（mac 不含）
    has_updater_exe: bool

    @classmethod
    def for_variant(cls, variant: str) -> "VariantConfig":
        """从 CLI 传入的变体名（main / noWinIME / mac / ""）构造配置。"""
        norm = "" if variant in ("main", "") else variant
        app_name = "StrangeUtaGame" if not norm else f"StrangeUtaGame-{norm}"
        return cls(
            variant=norm,
            app_name=app_name,
            dist_dir=ROOT / "dist" / app_name,
            runtime_hash_cache=ROOT / "scripts" / (
                ".runtime-hash-cache.json" if not norm
                else f".runtime-hash-cache-{norm}.json"
            ),
            runtime_latest_zip=ROOT / "dist" / (
                "runtime-latest.zip" if not norm
                else f"runtime-latest-{norm}.zip"
            ),
            has_updater_exe=(norm != "mac"),
        )

    # ── 资产命名 ──

    def asset_zip(self, version: str, suffix: str = "") -> Path:
        """返回全量或 part zip 的 Path。suffix 为空 → 全量；否则 → part（app/runtime）。"""
        if suffix:
            name = f"{self.app_name}-v{version}-{suffix}.zip"
        else:
            name = f"{self.app_name}-v{version}.zip"
        return RELEASE_DIST / name

    def manifest_name(self, version: str) -> str:
        """manifest JSON 文件名。"""
        if self.variant:
            return f"manifest-{self.variant}-v{version}.json"
        return f"manifest-v{version}.json"

    def app_targets_base(self) -> List[str]:
        """app part 的 targets 列表（EXE + Updater + 应用代码包）。"""
        targets: List[str] = []
        exe = f"{self.app_name}.exe" if sys.platform == "win32" else self.app_name
        targets.append(exe)
        if self.has_updater_exe and sys.platform == "win32":
            targets.append("Updater.exe")
        targets.append("_internal/strange_uta_game")
        return targets

    def label(self) -> str:
        """人类可读的变体标签。"""
        return self.variant or "main"


# ───────────────────────── prepare ─────────────────────────


def _check_version_format(value: str) -> str:
    if not VERSION_RE.match(value):
        raise SystemExit(f"版本号必须形如 X.Y.Z（收到 {value!r}）")
    return value


def _read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit(f"无法在 {VERSION_FILE} 中解析 __version__")
    return m.group(1)


def _write_version(new_version: str) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(__version__\s*=\s*")[^"]+(")',
        rf'\g<1>{new_version}\g<2>',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"未能在 {VERSION_FILE} 中替换 __version__")
    VERSION_FILE.write_text(new_text, encoding="utf-8")


_CHANGELOG_PLACEHOLDER = """## [{version}] - {date}

### 新增功能
- *（待补充）*

### 特性改变
- *（待补充）*

### 修复项目
- *（待补充）*

"""


def _has_version_section(content: str, version: str) -> bool:
    pattern = re.compile(rf'^##\s*\[{re.escape(version)}\]', re.MULTILINE)
    return bool(pattern.search(content))


def _insert_placeholder(version: str) -> bool:
    """如果 CHANGELOG 中没有对应版本段落则插入模板；返回是否真的插入了。"""
    if not CHANGELOG.exists():
        raise SystemExit(f"找不到 {CHANGELOG} —— 请先创建 CHANGELOG.md")
    content = CHANGELOG.read_text(encoding="utf-8")
    if _has_version_section(content, version):
        return False

    today = dt.date.today().isoformat()
    placeholder = _CHANGELOG_PLACEHOLDER.format(version=version, date=today)

    # 插在 ``## [Unreleased]`` 段之后；若不存在则插在文件顶部第一段之前
    m = re.search(r'^##\s*\[Unreleased\][^\n]*\n', content, re.MULTILINE)
    if m:
        # 找到下一个 ## 段落开头
        next_section = re.search(r'^## ', content[m.end():], re.MULTILINE)
        if next_section:
            insert_at = m.end() + next_section.start()
        else:
            insert_at = len(content)
        new_content = content[:insert_at] + placeholder + content[insert_at:]
    else:
        # 直接插在文件最上（标题之后）
        title_m = re.search(r'^#\s+Changelog[^\n]*\n', content, re.MULTILINE)
        insert_at = title_m.end() if title_m else 0
        new_content = content[:insert_at] + "\n" + placeholder + content[insert_at:]

    CHANGELOG.write_text(new_content, encoding="utf-8")
    return True


def cmd_prepare(version: str) -> int:
    version = _check_version_format(version)
    old = _read_version()
    if old == version:
        print(f"  __version__ 已是 {version}，跳过版本号写入")
    else:
        _write_version(version)
        print(f"  __version__ {old} → {version}")
    inserted = _insert_placeholder(version)
    if inserted:
        print(f"  已在 CHANGELOG.md 中插入 [{version}] 占位段落，请手动填写具体条目")
    else:
        print(f"  CHANGELOG.md 已存在 [{version}] 段落，未修改")
    print()
    print("✓ prepare 完成。后续步骤：")
    print(f"  1) 编辑 {CHANGELOG.relative_to(ROOT)}，把 [{version}] 段落补充完整")
    print(f"  2) python scripts\\release.py build  # 或 all {version}")
    return 0


# ───────────────────────── extract-notes ─────────────────────────


def _extract_section(version: str) -> str:
    content = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'(?ms)^##\s*\[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^##\s|\Z)',
    )
    m = pattern.search(content)
    if not m:
        raise SystemExit(f"CHANGELOG.md 中未找到 [{version}] 段落")
    body = m.group("body").rstrip() + "\n"
    return body


def cmd_extract_notes(version: str, output: Optional[Path]) -> int:
    version = _check_version_format(version)
    notes = _extract_section(version)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(notes, encoding="utf-8")
        print(f"✓ 已写入 {output}")
    else:
        sys.stdout.write(notes)
    return 0


# ───────────────────────── build ─────────────────────────


def _run_python(script: Path, args: Optional[list] = None) -> int:
    cmd = [sys.executable, str(script)] + list(args or [])
    print(f"  $ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(ROOT))


def _updater_sources_max_mtime() -> float:
    """返回 ``updater_app/`` 下所有 ``.py`` 文件的最大 mtime。"""
    src_dir = ROOT / "updater_app"
    mtimes: List[float] = []
    for p in src_dir.rglob("*.py"):
        rel = p.relative_to(src_dir)
        if rel.parts and rel.parts[0] in {"build", "dist"}:
            continue
        try:
            mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else 0.0


def _ensure_updater_exe(force: bool = False, clean: bool = False) -> None:
    """确保 ``Updater.exe`` 存在且不落后于源代码。

    mac 变体不含 Updater.exe，调用前应由 vcfg.has_updater_exe 判断是否跳过。
    """
    if force:
        print("  ! --rebuild-updater 强制重打 Updater.exe …")
        _do_rebuild_updater(clean=clean)
        return

    if not UPDATER_EXE.exists():
        print("  ! 未发现 updater_app/dist/Updater.exe，开始构建 …")
        _do_rebuild_updater(clean=clean)
        return

    exe_mtime = UPDATER_EXE.stat().st_mtime
    src_mtime = _updater_sources_max_mtime()
    if src_mtime > exe_mtime:
        import datetime as _dt
        exe_dt = _dt.datetime.fromtimestamp(exe_mtime).strftime("%Y-%m-%d %H:%M:%S")
        src_dt = _dt.datetime.fromtimestamp(src_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"  ! Updater.exe 已过期（exe mtime={exe_dt}, 源码 mtime={src_dt}），"
            f"重新打包 Updater.exe …"
        )
        _do_rebuild_updater(clean=clean)
        return

    size_mb = UPDATER_EXE.stat().st_size / 1024 / 1024
    print(f"  ✓ 已存在 Updater.exe，源码未更新（{size_mb:.1f} MB）")


def _do_rebuild_updater(clean: bool = False) -> None:
    extra = ["--clean"] if clean else []
    rc = _run_python(UPDATER_BUILD, extra)
    if rc != 0:
        raise SystemExit(f"构建 Updater 失败，退出码 {rc}")
    if not UPDATER_EXE.exists():
        raise SystemExit("构建似乎完成，但未在 updater_app/dist 下找到 Updater.exe")


def _verify_release_assets(version: str, vcfg: VariantConfig, full_zip: Path) -> None:
    """打完 build 后立刻自检每个产物是否到位，让"漏写文件"在 push 之前暴露。"""
    dist_root = vcfg.dist_dir
    parent = full_zip.parent
    required_release_assets = [
        full_zip,
        full_zip.with_name(full_zip.name + ".sha256"),
        vcfg.asset_zip(version, "app"),
        vcfg.asset_zip(version, "app").with_name(vcfg.asset_zip(version, "app").name + ".sha256"),
        vcfg.asset_zip(version, "runtime"),
        vcfg.asset_zip(version, "runtime").with_name(vcfg.asset_zip(version, "runtime").name + ".sha256"),
        parent / vcfg.manifest_name(version),
    ]
    missing: List[str] = []
    for p in required_release_assets:
        if not p.exists():
            missing.append(str(p.relative_to(ROOT)))

    # dist 内的"出厂本地清单"也必须存在
    installed_manifest = dist_root / "_internal" / ".installed_manifest.json"
    if not installed_manifest.exists():
        missing.append(str(installed_manifest.relative_to(ROOT)))

    # Updater.exe（仅 Windows 变体）
    if vcfg.has_updater_exe and sys.platform == "win32":
        updater_in_dist = dist_root / "Updater.exe"
        if not updater_in_dist.exists():
            missing.append(str(updater_in_dist.relative_to(ROOT)))

    # strange_uta_game.updater 子包
    updater_pkg = dist_root / "_internal" / "strange_uta_game" / "updater"
    if not updater_pkg.is_dir():
        missing.append(str(updater_pkg.relative_to(ROOT)) + "/  (updater 子包缺失)")

    if missing:
        print("  ✗ 自检失败，以下文件缺失：")
        for m in missing:
            print(f"      • {m}")
        raise SystemExit(
            "构建产物不完整。常见原因：在 release.py build 之后又单独跑了一次 "
            f"`python build.py --variant {vcfg.label()}`，PyInstaller --noconfirm 会清空 "
            f"dist/{vcfg.app_name}/ 把 .installed_manifest.json 等文件一并删除。"
        )

    print("  ✓ 所有发布资产就绪：")
    for p in required_release_assets:
        rel = p.relative_to(ROOT)
        size_mb = p.stat().st_size / 1024 / 1024
        print(f"      • {rel}  ({size_mb:.2f} MB)")
    print(f"      • {installed_manifest.relative_to(ROOT)}  (出厂本地清单)")
    if vcfg.has_updater_exe and sys.platform == "win32":
        updater_in_dist = dist_root / "Updater.exe"
        print(f"      • {updater_in_dist.relative_to(ROOT)}  (Updater.exe 已就位)")
    n_updater_files = len(list(updater_pkg.iterdir()))
    print(f"      • {updater_pkg.relative_to(ROOT)}/  (updater 子包，{n_updater_files} 文件)")


def _run_main_build(clean: bool = False, variant: str = "") -> None:
    print(f"  构建主程序 (PyInstaller --onedir, variant={variant or 'main'}) …")
    extra: List[str] = []
    if clean:
        extra.append("--clean")
    if variant and variant != "main":
        extra.extend(["--variant", variant])
    rc = _run_python(MAIN_BUILD, extra)
    if rc != 0:
        raise SystemExit(f"主程序构建失败，退出码 {rc}")


def _pack_zip(version: str, vcfg: VariantConfig) -> Path:
    dist_root = vcfg.dist_dir
    if not dist_root.exists():
        raise SystemExit(f"主程序产物目录不存在: {dist_root}")
    zip_path = vcfg.asset_zip(version)
    if zip_path.exists():
        zip_path.unlink()
    print(f"  打包 {dist_root.name}/ → {zip_path.name}")
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in dist_root.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(dist_root.parent))
    print(f"  ✓ {zip_path}  ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return zip_path


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _content_hash_of_zip(zip_path: Path) -> str:
    """计算 zip 内所有文件的内容哈希（确定性，不受打包元数据影响）。"""
    entries: List[tuple[str, str]] = []
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            content = zf.read(info.filename)
            content_hash = hashlib.sha256(content).hexdigest()
            entries.append((info.filename, content_hash))
    entries.sort(key=lambda e: e[0])
    combined = "\n".join(f"{name}:{h}" for name, h in entries)
    return hashlib.sha256(combined.encode("ascii")).hexdigest().lower()


def _write_sha256(target: Path) -> Path:
    """为 ``target`` 生成同名 ``.sha256`` 文件，与 sha256sum / coreutils 兼容。"""
    digest = _sha256_of(target)
    sha_path = target.with_name(target.name + ".sha256")
    sha_path.write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(f"  ✓ {sha_path.name}  (sha256={digest})")
    return sha_path


# ───────────────────────── runtime 哈希缓存 ─────────────────────────


def _load_runtime_cache(cache_path: Path) -> Optional[Dict]:
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_freeze_excludes() -> List[str]:
    """加载排除包名前缀列表（仅在 _requirements_runtime_lines 中使用）。"""
    if RUNTIME_FREEZE_EXCLUDE.exists():
        lines = RUNTIME_FREEZE_EXCLUDE.read_text(encoding="utf-8").splitlines()
        return [l.strip().lower() for l in lines if l.strip() and not l.startswith("#")]
    return [p.lower() for p in _DEFAULT_FREEZE_EXCLUDES]


def _pkg_name_normalize(raw: str) -> str:
    """把包名规范化为小写、连字符形式（pip/PyPI 规范）。"""
    return raw.strip().split("==")[0].split(" @ ")[0].lower().replace("_", "-")


def _requirements_runtime_lines() -> List[str]:
    """读取 requirements.txt，返回属于运行时依赖的锁定行（排除开发工具）。"""
    if not REQUIREMENTS_FILE.exists():
        return []
    excludes = _load_freeze_excludes()
    result: List[str] = []
    for raw in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pkg = _pkg_name_normalize(line)
        if any(pkg == ex or pkg.startswith(ex + "-") for ex in excludes):
            continue
        result.append(line)
    return result


def _requirements_hash() -> str:
    """对 requirements.txt 的运行时行取 SHA-256。"""
    lines = _requirements_runtime_lines()
    if not lines:
        return ""
    text = "\n".join(sorted(lines))
    return hashlib.sha256(text.encode("utf-8")).hexdigest().lower()


def _diff_requirements(old_lines: List[str], new_lines: List[str]) -> str:
    """返回两份 requirements 行之间的新增/删除/变更（供人工确认用）。"""
    old_set = {_pkg_name_normalize(l): l for l in old_lines if l.strip()}
    new_set = {_pkg_name_normalize(l): l for l in new_lines if l.strip()}
    added   = [new_set[k] for k in new_set if k not in old_set]
    removed = [old_set[k] for k in old_set if k not in new_set]
    changed = [f"{old_set[k]}  →  {new_set[k]}" for k in new_set
               if k in old_set and old_set[k] != new_set[k]]
    parts: List[str] = []
    if added:
        parts.append("  新增: " + ", ".join(added))
    if removed:
        parts.append("  移除: " + ", ".join(removed))
    if changed:
        parts.append("  变更:\n    " + "\n    ".join(changed))
    return "\n".join(parts) if parts else "  （无差异）"


def _scan_dist_packages(dist_root: Optional[Path] = None) -> Dict[str, str]:
    """扫描 ``dist_root/_internal/*.dist-info``，返回 ``{规范名: 版本}``。"""
    if dist_root is None:
        dist_root = MAIN_DIST
    internal = dist_root / "_internal"
    if not internal.is_dir():
        return {}
    result: Dict[str, str] = {}
    for dist_info in internal.glob("*.dist-info"):
        metadata_file = dist_info / "METADATA"
        if not metadata_file.exists():
            metadata_file = dist_info / "PKG-INFO"
        if not metadata_file.exists():
            continue
        name = version = ""
        try:
            for line in metadata_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip().lower().replace("_", "-")
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                if name and version:
                    break
        except OSError:
            continue
        if name and version:
            result[name] = version
    return result


def _current_installed_versions(pkg_names: List[str]) -> Dict[str, str]:
    """查询当前环境中指定包的已安装版本，返回 ``{规范名: 版本}``。"""
    import importlib.metadata as _meta

    result: Dict[str, str] = {}
    for raw_name in pkg_names:
        norm = raw_name.lower().replace("_", "-")
        found = False
        for probe in (norm, norm.replace("-", "_")):
            try:
                result[norm] = _meta.version(probe)
                found = True
                break
            except _meta.PackageNotFoundError:
                continue
        if not found:
            result[norm] = ""
    return result


def _diff_dist_packages(
    cached: Dict[str, str],
    current: Dict[str, str],
) -> str:
    """对比缓存与当前环境的包版本，返回人可读的 diff 字符串。"""
    added   = [f"{k}=={v}" for k, v in current.items() if k not in cached]
    removed = [f"{k}=={cached[k]}" for k in cached if k not in current]
    changed = [
        f"{k}: {cached[k]}  →  {v}"
        for k, v in current.items()
        if k in cached and cached[k] != v and v
    ]
    parts: List[str] = []
    if added:
        parts.append("  新增: " + ", ".join(sorted(added)))
    if removed:
        parts.append("  移除: " + ", ".join(sorted(removed)))
    if changed:
        parts.append("  变更:\n    " + "\n    ".join(sorted(changed)))
    return "\n".join(parts) if parts else "  （无差异）"


def _save_runtime_cache(
    cache_path: Path,
    version: str,
    content_hash: str,
    size: int,
    requirements_hash: str = "",
    req_lines: Optional[List[str]] = None,
    dist_packages: Optional[Dict[str, str]] = None,
) -> None:
    data = {
        "version": version,
        "content_hash": content_hash,
        "size": size,
        "requirements_hash": requirements_hash,
        "req_lines": list(req_lines) if req_lines else [],
        "dist_packages": dist_packages if dist_packages is not None else {},
    }
    cache_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  ✓ 已更新 runtime 哈希缓存: {cache_path.relative_to(ROOT)}")
    print("  ⚠ 记得把 scripts/.runtime-hash-cache*.json 一并提交到 git！")


# ───────────────────────── 增量打包：app + runtime 分包 ─────────────────────────

# 注意：以下字面量需与 updater_app/main.py 中的同名常量保持同步：
#   UPDATER_EXE_NAME          = "Updater.exe"
#   LOCAL_MANIFEST_FILENAME   = ".installed_manifest.json"
INTERNAL_NON_RUNTIME_NAMES = {
    "strange_uta_game",
    ".installed_manifest.json",
}


def _compute_runtime_targets(dist_root: Path) -> List[str]:
    """扫描 ``dist_root/_internal/`` 把不属于 app 的所有顶层条目列为 runtime targets。"""
    internal_dir = dist_root / "_internal"
    if not internal_dir.is_dir():
        return []
    out: List[str] = []
    for child in sorted(internal_dir.iterdir(), key=lambda p: p.name.lower()):
        if child.name in INTERNAL_NON_RUNTIME_NAMES:
            continue
        out.append(f"_internal/{child.name}")
    return out


def _pack_part_zip(zip_path: Path, dist_root: Path, targets: List[str]) -> None:
    """把 ``dist_root`` 下 targets 列出的内容打成一个 zip。"""
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for t in targets:
            src = dist_root / t
            if not src.exists():
                print(f"  ! 跳过不存在的 target: {t}")
                continue
            if src.is_file():
                zf.write(src, arcname=t)
            else:
                for f in src.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(dist_root)
                        zf.write(f, arcname=str(rel).replace("\\", "/"))


def _pack_parts(
    version: str,
    vcfg: VariantConfig,
    rebuild_runtime: bool = False,
    reuse_runtime: bool = False,
) -> Tuple[Path, Path, List[str], List[str]]:
    """打 app + runtime 两个 part zip 并生成各自 .sha256 文件。

    返回 ``(app_zip_path, runtime_zip_path, app_targets, runtime_targets)``。

    runtime 判断策略（每个变体有独立的缓存文件）：
    * ``reuse_runtime=True``   无条件复用上次 runtime zip
    * ``rebuild_runtime=True`` 无条件重新打包
    * 两者均 False（默认）     自动比对 dist-info / requirements.txt hash
    """
    dist_root = vcfg.dist_dir
    cache_path = vcfg.runtime_hash_cache
    runtime_latest = vcfg.runtime_latest_zip

    app_targets = vcfg.app_targets_base()
    runtime_targets = _compute_runtime_targets(dist_root)

    app_zip = vcfg.asset_zip(version, "app")
    runtime_zip = vcfg.asset_zip(version, "runtime")

    # ── app part（始终重新打包）──
    print(f"  打包 app part → {app_zip.name}（{len(app_targets)} targets）")
    _pack_part_zip(app_zip, dist_root, app_targets)
    print(f"  ✓ {app_zip.name}  ({app_zip.stat().st_size / 1024 / 1024:.1f} MB)")
    _write_sha256(app_zip)

    # ── runtime part：自动判断是否可复用 ──
    reused = False

    if reuse_runtime and not rebuild_runtime:
        prev_zip: Optional[Path] = runtime_latest if runtime_latest.exists() else None
        if prev_zip is None:
            cache = _load_runtime_cache(cache_path)
            prev_version = (cache or {}).get("version", "")
            candidate = vcfg.asset_zip(prev_version, "runtime")
            if candidate.exists():
                prev_zip = candidate
        if prev_zip is not None:
            _cache_for_reuse = _load_runtime_cache(cache_path) or {}
            prev_hash = _cache_for_reuse.get("content_hash", "")
            print(f"  --reuse-runtime：无条件复用 {prev_zip.name} → {runtime_zip.name}")
            shutil.copy2(str(prev_zip), str(runtime_zip))
            print(f"  ✓ {runtime_zip.name}  ({runtime_zip.stat().st_size / 1024 / 1024:.1f} MB)")
            _write_sha256(runtime_zip)
            _save_runtime_cache(
                cache_path, version, prev_hash,
                runtime_zip.stat().st_size,
                _cache_for_reuse.get("requirements_hash", ""),
                _cache_for_reuse.get("req_lines"),
                dist_packages=_cache_for_reuse.get("dist_packages"),
            )
            reused = True
        else:
            print("  ! --reuse-runtime：找不到可用的 runtime zip，将重新打包")

    if not reused and not rebuild_runtime:
        cache = _load_runtime_cache(cache_path)
        current_req_lines = _requirements_runtime_lines()
        current_req_hash = _requirements_hash()

        cached_dist_pkgs: Dict[str, str] = (cache or {}).get("dist_packages", {})
        current_dist_pkgs: Dict[str, str] = _scan_dist_packages(dist_root)
        dep_diff = ""

        if cached_dist_pkgs and current_dist_pkgs:
            if cached_dist_pkgs == current_dist_pkgs:
                dep_changed = False
                dep_reason = (
                    f"dist-info 包版本与上次构建完全吻合"
                    f"（{len(current_dist_pkgs)} 个包）"
                )
            else:
                dep_changed = True
                dep_reason = "dist-info 包版本已变化（新构建与缓存不同）"
                dep_diff = _diff_dist_packages(cached_dist_pkgs, current_dist_pkgs)

        elif cache is not None:
            if cache.get("requirements_hash") == current_req_hash:
                dep_changed = False
                dep_reason = "requirements.txt hash 未变（旧缓存格式）"
            else:
                dep_changed = True
                dep_reason = "requirements.txt 运行时依赖已变化（旧缓存格式）"
                dep_diff = _diff_requirements(
                    cache.get("req_lines", []), current_req_lines
                )

        else:
            dep_changed = True
            dep_reason = "无缓存记录（首次构建），打包 runtime 并建立缓存"

        if not dep_changed:
            prev_hash = (cache or {}).get("content_hash", "")
            prev_zip_auto: Optional[Path] = runtime_latest if runtime_latest.exists() else None
            if prev_zip_auto is None:
                prev_version = (cache or {}).get("version", "")
                candidate = vcfg.asset_zip(prev_version, "runtime")
                if candidate.exists():
                    prev_zip_auto = candidate
            if prev_hash and prev_zip_auto is not None:
                print(f"  {dep_reason}")
                print(f"  复用 {prev_zip_auto.name} → {runtime_zip.name}")
                shutil.copy2(str(prev_zip_auto), str(runtime_zip))
                print(
                    f"  ✓ {runtime_zip.name}"
                    f"  ({runtime_zip.stat().st_size / 1024 / 1024:.1f} MB)"
                    f"  [content hash 与上次相同，用户不会重新下载]"
                )
                _write_sha256(runtime_zip)
                reuse_dist_pkgs = (cache or {}).get("dist_packages") or current_dist_pkgs
                _save_runtime_cache(
                    cache_path, version, prev_hash, runtime_zip.stat().st_size,
                    current_req_hash, current_req_lines,
                    dist_packages=reuse_dist_pkgs,
                )
                reused = True
            else:
                reason_no_zip = "content_hash 缺失" if not prev_hash else "runtime-latest.zip 不存在"
                print(f"  ! {dep_reason}，但缓存 zip 不可用（{reason_no_zip}），重新打包 runtime")
        else:
            print(f"  {dep_reason}，重新打包 runtime")
            if dep_diff:
                print("  变化详情：")
                print(dep_diff)

    if rebuild_runtime:
        print("  --rebuild-runtime：强制重新打包 runtime")

    if not reused:
        print(f"  打包 runtime part → {runtime_zip.name}（{len(runtime_targets)} targets）")
        _pack_part_zip(runtime_zip, dist_root, runtime_targets)
        print(f"  ✓ {runtime_zip.name}  ({runtime_zip.stat().st_size / 1024 / 1024:.1f} MB)")
        _write_sha256(runtime_zip)
        content_hash = _content_hash_of_zip(runtime_zip)
        req_hash = _requirements_hash()
        req_lines = _requirements_runtime_lines()
        new_dist_pkgs = _scan_dist_packages(dist_root)
        if new_dist_pkgs:
            pkg_list = ", ".join(f"{k}=={v}" for k, v in sorted(new_dist_pkgs.items()))
            print(f"  ✓ 扫描 dist-info：{len(new_dist_pkgs)} 个包 → {pkg_list}")
        else:
            print("  ! 未在 _internal/ 中找到任何 .dist-info，runtime 变更检测将降级到 requirements.txt")
        _save_runtime_cache(cache_path, version, content_hash, runtime_zip.stat().st_size,
                            req_hash, req_lines, dist_packages=new_dist_pkgs)
        shutil.copy2(str(runtime_zip), str(runtime_latest))
        print(f"  ✓ 已更新 runtime 备份: {runtime_latest.relative_to(ROOT)}")

    return app_zip, runtime_zip, app_targets, runtime_targets


def _write_installed_manifest_into_dist(
    version: str,
    vcfg: VariantConfig,
    app_zip: Path,
    runtime_zip: Path,
    app_targets: Optional[List[str]] = None,
    runtime_targets: Optional[List[str]] = None,
) -> Path:
    """把"出厂版本"的 .installed_manifest.json 直接写到 ``dist/<app_name>/_internal/``。"""
    dist_root = vcfg.dist_dir
    payload = {
        "version": version,
        "schema": 1,
        "parts": {
            "app": {
                "sha256": _content_hash_of_zip(app_zip),
                "asset": app_zip.name,
                "targets": list(app_targets or []),
            },
            "runtime": {
                "sha256": _content_hash_of_zip(runtime_zip),
                "asset": runtime_zip.name,
                "targets": list(runtime_targets or []),
            },
        },
        "installed_at": int(_time.time()),
    }
    p = dist_root / "_internal" / ".installed_manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  ✓ 已写入出厂本地清单: {p.relative_to(dist_root.parent)}")
    return p


def _write_release_manifest(
    version: str,
    vcfg: VariantConfig,
    full_zip: Path,
    app_zip: Path,
    runtime_zip: Path,
    app_targets: List[str],
    runtime_targets: List[str],
) -> Path:
    """生成对外发布的 ``manifest-[variant-]vX.Y.Z.json``。"""
    manifest: Dict = {
        "version": version,
        "schema": 1,
        "parts": {
            "app": {
                "asset": app_zip.name,
                "sha256": _content_hash_of_zip(app_zip),
                "size": app_zip.stat().st_size,
                "targets": app_targets,
            },
            "runtime": {
                "asset": runtime_zip.name,
                "sha256": _content_hash_of_zip(runtime_zip),
                "size": runtime_zip.stat().st_size,
                "targets": runtime_targets,
            },
        },
        "full": {
            "asset": full_zip.name,
            "sha256": _content_hash_of_zip(full_zip),
            "size": full_zip.stat().st_size,
        },
    }
    manifest_path = RELEASE_DIST / vcfg.manifest_name(version)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  ✓ {manifest_path.name}")
    return manifest_path


def _dump_release_notes(version: str) -> Optional[Path]:
    try:
        notes = _extract_section(version)
    except SystemExit as e:
        print(f"  ! 跳过抽取 release_notes：{e}")
        return None
    notes_path = RELEASE_DIST / f"release_notes-v{version}.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes, encoding="utf-8")
    print(f"  ✓ {notes_path}")
    return notes_path


def cmd_build(
    rebuild_updater: bool = False,
    clean: bool = False,
    rebuild_runtime: bool = False,
    reuse_runtime: bool = False,
    variant: str = "",
) -> int:
    vcfg = VariantConfig.for_variant(variant)
    version = _read_version()
    print(f"== build for v{version}  variant={vcfg.label()} ==")

    # 1. Updater.exe（仅 Windows 变体）
    if vcfg.has_updater_exe and sys.platform == "win32":
        _ensure_updater_exe(force=rebuild_updater, clean=clean)
    else:
        print(f"  ✓ 跳过 Updater.exe 构建（variant={vcfg.label()}，当前平台={sys.platform}）")

    # 2. 主程序
    _run_main_build(clean=clean, variant=vcfg.variant)

    # 关键顺序：
    #   1) 打 app + runtime part zip（不含 .installed_manifest.json）→ 算 sha256
    #   2) 写 .installed_manifest.json 到 dist/<app>/_internal/
    #   3) 打全量 zip（含 .installed_manifest.json）→ 算 sha256
    #   4) 写对外发布的 manifest-[variant-]vX.Y.Z.json
    print()
    print("[step] 打增量分包 part zip ...")
    app_zip, runtime_zip, app_targets, runtime_targets = _pack_parts(
        version, vcfg, rebuild_runtime=rebuild_runtime, reuse_runtime=reuse_runtime
    )

    print()
    print("[step] 写出厂本地清单到 _internal/.installed_manifest.json ...")
    _write_installed_manifest_into_dist(version, vcfg, app_zip, runtime_zip, app_targets, runtime_targets)

    print()
    print("[step] 打全量 zip（含本地清单）...")
    zip_path = _pack_zip(version, vcfg)
    sha_path = _write_sha256(zip_path)

    print()
    print(f"[step] 写对外发布 {vcfg.manifest_name(version)} ...")
    manifest_path = _write_release_manifest(
        version, vcfg, zip_path, app_zip, runtime_zip, app_targets, runtime_targets,
    )

    notes_path = _dump_release_notes(version)

    print()
    print("[step] 完整性自检 ...")
    _verify_release_assets(version, vcfg=vcfg, full_zip=zip_path)

    print()
    print(f"✓ 打包完成（variant={vcfg.label()}）。请手动执行后续步骤：")
    print()
    print(f"⚠ 重要：不要再单独跑 `python build.py --variant {vcfg.label()}`，")
    print(f"   那会清空 dist/{vcfg.app_name}/，把刚刚写入的 .installed_manifest.json 一并删除。")
    print()
    print(f"  git add -A")
    print(f'  git commit -m "release v{version}"')
    print(f"  git tag SUGv{version}")
    print(f"  git push origin main --tags")
    print()
    print("然后到 GitHub Web 创建 Release：")
    print(f"  - tag: SUGv{version}")
    print(f"  - 标题：v{version}")
    if notes_path:
        print(f"  - body：直接复制 {notes_path.relative_to(ROOT)} 全文")
    print(f"  - 资产（全部上传）：")
    print(f"      • {zip_path.relative_to(ROOT)}            ← 全量包")
    print(f"      • {sha_path.relative_to(ROOT)}     ← 全量包校验")
    print(f"      • {manifest_path.relative_to(ROOT)}   ← 增量更新清单")
    for suffix in ("app", "runtime"):
        for ext in ("", ".sha256"):
            p = vcfg.asset_zip(version, suffix)
            if ext:
                p = p.with_name(p.name + ext)
            if p.exists():
                print(f"      • {p.relative_to(ROOT)}")
    return 0


def cmd_all(
    version: str,
    rebuild_updater: bool = False,
    clean: bool = False,
    rebuild_runtime: bool = False,
    reuse_runtime: bool = False,
    variant: str = "",
) -> int:
    rc = cmd_prepare(version)
    if rc != 0:
        return rc
    print()
    print("⚠ prepare 已完成。在继续 build 之前，建议先编辑 CHANGELOG.md 补充内容。")
    answer = input("是否继续 build？(y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消 build。")
        return 0
    return cmd_build(
        rebuild_updater=rebuild_updater, clean=clean,
        rebuild_runtime=rebuild_runtime, reuse_runtime=reuse_runtime,
        variant=variant,
    )


# ───────────────────────── entry ─────────────────────────

_VARIANT_CHOICES = ["main", "noWinIME", "mac"]


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_prepare = sub.add_parser("prepare", help="改 __version__ 并注入 CHANGELOG 占位段落")
    sp_prepare.add_argument("version", help="目标版本号 X.Y.Z")

    sp_extract = sub.add_parser("extract-notes", help="抽取 CHANGELOG 段落")
    sp_extract.add_argument("version", help="版本号 X.Y.Z")
    sp_extract.add_argument("-o", "--output", type=Path, default=None)

    sp_build = sub.add_parser("build", help="跑完整构建（Updater + 主程序 + zip）")
    sp_build.add_argument(
        "--variant",
        choices=_VARIANT_CHOICES,
        default="main",
        help="构建变体：main（默认，Windows+WinRT）/ noWinIME（Windows,无WinRT）/ mac",
    )
    sp_build.add_argument(
        "--rebuild-updater",
        action="store_true",
        help="强制重新打包 Updater.exe，即便源码 mtime 未变",
    )
    sp_build.add_argument(
        "--clean",
        action="store_true",
        help="传给 PyInstaller --clean，完整重建",
    )
    sp_build.add_argument(
        "--rebuild-runtime",
        action="store_true",
        help="强制重新打包 runtime zip，忽略缓存自动判断",
    )
    sp_build.add_argument(
        "--reuse-runtime",
        action="store_true",
        help="无条件复用上次打包的 runtime zip，跳过 hash 检查",
    )

    sp_all = sub.add_parser("all", help="prepare + build")
    sp_all.add_argument("version", help="目标版本号 X.Y.Z")
    sp_all.add_argument(
        "--variant",
        choices=_VARIANT_CHOICES,
        default="main",
        help="构建变体",
    )
    sp_all.add_argument("--rebuild-updater", action="store_true")
    sp_all.add_argument("--clean", action="store_true")
    sp_all.add_argument("--rebuild-runtime", action="store_true")
    sp_all.add_argument("--reuse-runtime", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args.version)
    if args.cmd == "extract-notes":
        return cmd_extract_notes(args.version, args.output)
    if args.cmd == "build":
        return cmd_build(
            rebuild_updater=args.rebuild_updater,
            clean=args.clean,
            rebuild_runtime=args.rebuild_runtime,
            reuse_runtime=args.reuse_runtime,
            variant=args.variant,
        )
    if args.cmd == "all":
        return cmd_all(
            args.version,
            rebuild_updater=args.rebuild_updater,
            clean=args.clean,
            rebuild_runtime=args.rebuild_runtime,
            reuse_runtime=args.reuse_runtime,
            variant=args.variant,
        )
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

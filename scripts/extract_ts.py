"""提取 tr 源串并生成 Qt .ts（UTF-8 安全）。

pylupdate6 在 Windows 上按系统默认 codec (gbk) 读源码，UTF-8 中文被错位编码
成乱码（"璇█" 之类）。本脚本用 ast 走源码、严格 UTF-8 读，生成结构与 Qt
lrelease 兼容的 .ts，再由 `compile_qm.py` 或 lrelease 编译为 .qm。

匹配模式：
- `self.tr("...")` / `obj.tr("...")` —— 上下文取所在类名（无类时取 "Global"）
- `QCoreApplication.translate("Ctx", "...")` —— 上下文用显式第一个参数
- 同时支持隐式拼接的字符串：``self.tr("a" "b")`` → "ab"

未匹配（视为代码逻辑而非可翻译文本）：
- 变量入参的 tr：``self.tr(some_var)`` —— 无法静态求值
- f-string —— 应在源代码层改成 tr+format
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from xml.sax.saxutils import escape


def _extract_string(node: ast.AST) -> str | None:
    """从 ast 节点恢复字符串字面量；隐式拼接也支持。

    支持 ``"a" "b"`` 在 AST 里出现的形式（已折叠成单个 Constant str）；
    若是 BinOp("+") 拼接也尝试求值。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return None  # f-string，跳过
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _extract_string(node.left)
        right = _extract_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _detect_tr_alias_context(tree: ast.AST) -> dict[str, str]:
    """扫描模块/函数体里 ``def _tr(s): return QCoreApplication.translate("Ctx", s)``
    或 ``_tr = lambda s: QCoreApplication.translate("Ctx", s)`` 的定义，
    返回 {别名: 上下文}。``_tr(...)`` 调用按这张表落到正确上下文。"""
    out: dict[str, str] = {}

    def _ctx_from_call(call: ast.Call) -> str | None:
        if (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "translate"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "QCoreApplication"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ):
            return call.args[0].value
        return None

    for node in ast.walk(tree):
        # def _tr(s): return QCoreApplication.translate("Ctx", s)
        if isinstance(node, ast.FunctionDef) and node.name in ("_tr", "tr"):
            for stmt in node.body:
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
                    ctx = _ctx_from_call(stmt.value)
                    if ctx:
                        out[node.name] = ctx
                        break
        # _tr = lambda s: QCoreApplication.translate("Ctx", s)
        if isinstance(node, ast.Assign):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("_tr", "tr")
                and isinstance(node.value, ast.Lambda)
                and isinstance(node.value.body, ast.Call)
            ):
                ctx = _ctx_from_call(node.value.body)
                if ctx:
                    out[node.targets[0].id] = ctx
    return out


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, alias_ctx: dict[str, str] | None = None) -> None:
        self.path = path
        self.class_stack: list[str] = []
        self.alias_ctx: dict[str, str] = alias_ctx or {}
        # (context, source, line)
        self.entries: list[tuple[str, str, int]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _current_context(self) -> str:
        return self.class_stack[-1] if self.class_stack else "Global"

    def visit_Call(self, node: ast.Call) -> None:
        # ── 形如 something.tr("...") ──────────────────────────
        if isinstance(node.func, ast.Attribute) and node.func.attr == "tr":
            if node.args:
                s = _extract_string(node.args[0])
                if s:
                    self.entries.append((self._current_context(), s, node.lineno))
        # ── 裸名 tr("...") / _tr("...")（常见做法：``tr = self.tr`` 别名）─
        elif isinstance(node.func, ast.Name) and node.func.id in ("tr", "_tr"):
            if node.args:
                s = _extract_string(node.args[0])
                if s:
                    # 若文件里有 ``_tr = lambda s: QCoreApplication.translate("Ctx", s)``
                    # 或 ``def _tr(s)`` 走 translate，则按该 Ctx 归类
                    ctx = self.alias_ctx.get(node.func.id, self._current_context())
                    self.entries.append((ctx, s, node.lineno))
        # ── QCoreApplication.translate("Ctx", "...") ──────────
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "translate"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "QCoreApplication"
            and len(node.args) >= 2
        ):
            ctx = _extract_string(node.args[0])
            s = _extract_string(node.args[1])
            if ctx and s:
                self.entries.append((ctx, s, node.lineno))
        self.generic_visit(node)


def scan_dir(root: Path) -> dict[str, dict[str, list[tuple[Path, int]]]]:
    """返回 {context: {source: [(file, lineno), ...]}}"""
    out: dict[str, dict[str, list[tuple[Path, int]]]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"skip non-utf8: {path}", file=sys.stderr)
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            print(f"skip syntax-error: {path}: {e}", file=sys.stderr)
            continue
        alias_ctx = _detect_tr_alias_context(tree)
        v = _Visitor(path, alias_ctx=alias_ctx)
        v.visit(tree)
        for ctx, s, line in v.entries:
            out.setdefault(ctx, {}).setdefault(s, []).append((path, line))
    return out


def write_ts(
    catalog: dict[str, dict[str, list[tuple[Path, int]]]],
    ts_path: Path,
    src_root: Path,
    *,
    language: str = "ja_JP",
    existing_translations: dict[tuple[str, str], str] | None = None,
) -> int:
    """写 .ts；返回写入条数。"""
    existing_translations = existing_translations or {}
    n = 0
    lines: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!DOCTYPE TS>",
        f'<TS version="2.1" language="{language}">',
    ]
    for ctx in sorted(catalog.keys()):
        lines.append("<context>")
        lines.append(f"    <name>{escape(ctx)}</name>")
        for source in sorted(catalog[ctx].keys()):
            locs = catalog[ctx][source]
            lines.append("    <message>")
            for path, line in locs:
                rel = (
                    path.resolve().relative_to(Path.cwd()).as_posix()
                    if path.resolve().is_relative_to(Path.cwd())
                    else path.as_posix()
                )
                lines.append(f'        <location filename="{escape(rel)}" line="{line}"/>')
            lines.append(f"        <source>{escape(source)}</source>")
            tr = existing_translations.get((ctx, source))
            if tr:
                lines.append(f"        <translation>{escape(tr)}</translation>")
            else:
                lines.append('        <translation type="unfinished"></translation>')
            lines.append("    </message>")
            n += 1
        lines.append("</context>")
    lines.append("</TS>")
    ts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


def load_existing(ts_path: Path) -> dict[tuple[str, str], str]:
    """从已存在 .ts 收集 (context, source) → translation，保留人手翻译。"""
    if not ts_path.exists():
        return {}
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(ts_path)
    except ET.ParseError:
        return {}
    root = tree.getroot()
    out: dict[tuple[str, str], str] = {}
    for ctx_el in root.findall("context"):
        name_el = ctx_el.find("name")
        if name_el is None or not name_el.text:
            continue
        ctx = name_el.text
        for msg in ctx_el.findall("message"):
            src_el = msg.find("source")
            tr_el = msg.find("translation")
            if src_el is None or tr_el is None or not src_el.text:
                continue
            tr = tr_el.text or ""
            unfinished = tr_el.get("type") == "unfinished"
            if tr and not unfinished:
                out[(ctx, src_el.text)] = tr
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--src", type=Path, default=Path("src/strange_uta_game"))
    p.add_argument(
        "--ts",
        type=Path,
        default=Path("src/strange_uta_game/frontend/localization/translations/app.ja_JP.ts"),
    )
    p.add_argument("--language", default="ja_JP")
    args = p.parse_args()

    catalog = scan_dir(args.src)
    existing = load_existing(args.ts)
    n = write_ts(catalog, args.ts, args.src, language=args.language, existing_translations=existing)
    print(f"wrote {n} messages across {len(catalog)} contexts → {args.ts}")
    if existing:
        print(f"preserved {len(existing)} existing translations")


if __name__ == "__main__":
    main()

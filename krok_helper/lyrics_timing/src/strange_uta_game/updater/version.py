"""语义化版本号比较与 tag 解析。

不依赖外部库（``packaging`` 可能未必随 PyInstaller 打包），实现一个足够覆盖
``X.Y.Z[-suffix]`` 形式的简单比较。
"""

from __future__ import annotations

import re
from typing import List, Tuple

from ..__version__ import TAG_PREFIX

# 形如 "0.3.2"、"0.3.2-beta1"、"v0.3.2" 的版本号。
# 主流程只关心 ``X.Y.Z`` 三段；suffix 部分按字符串比较（pre-release 视为更小）。
_VERSION_RE = re.compile(
    r"""
    ^
    [vV]?                           # 可选 v / V 前缀
    (?P<major>\d+)
    \.(?P<minor>\d+)
    (?:\.(?P<patch>\d+))?
    (?P<suffix>[-+.][0-9A-Za-z\-+.]+)?
    $
    """,
    re.VERBOSE,
)


def parse_version(value: str) -> Tuple[int, int, int, str]:
    """把 ``"0.3.2"`` / ``"v0.3.2-beta"`` 解析为可比较元组。

    无法解析时返回 ``(0, 0, 0, "")``，让上游降级为"无效版本"处理。
    """
    if not value:
        return (0, 0, 0, "")
    m = _VERSION_RE.match(value.strip())
    if not m:
        return (0, 0, 0, "")
    return (
        int(m.group("major")),
        int(m.group("minor")),
        int(m.group("patch") or 0),
        (m.group("suffix") or "").lstrip("-+."),
    )


def is_newer_version(remote: str, local: str) -> bool:
    """判断 ``remote`` 是否比 ``local`` 更新。

    比较规则：
    1. 按 ``(major, minor, patch)`` 三段整数比较；
    2. 三段相同时，没有 suffix 的版本视为"更新"（正式版 > 预发布）；
    3. 两边都有 suffix，则按字符串 ``str`` 排序比较。
    """
    rmaj, rmin, rpat, rsfx = parse_version(remote)
    lmaj, lmin, lpat, lsfx = parse_version(local)
    if (rmaj, rmin, rpat) != (lmaj, lmin, lpat):
        return (rmaj, rmin, rpat) > (lmaj, lmin, lpat)
    # 三段一致时
    if rsfx == lsfx:
        return False
    if not rsfx:  # remote 是正式版，本地是 pre
        return True
    if not lsfx:  # remote 是 pre，本地是正式版
        return False
    return rsfx > lsfx


def strip_tag_prefix(tag: str) -> str:
    """从 GitHub release tag 还原出纯版本号。

    例：``"SUGv0.3.2"`` → ``"0.3.2"``，``"v0.3.2"`` → ``"0.3.2"``，
    ``"0.3.2"`` 原样返回。
    """
    s = (tag or "").strip()
    if not s:
        return ""
    if s.startswith(TAG_PREFIX):
        s = s[len(TAG_PREFIX):]
    if s.startswith("v") or s.startswith("V"):
        s = s[1:]
    return s


def split_version_list(text: str) -> List[str]:
    """工具方法：把"跳过的版本"配置（逗号分隔字符串）拆为列表。"""
    return [s.strip() for s in (text or "").split(",") if s.strip()]

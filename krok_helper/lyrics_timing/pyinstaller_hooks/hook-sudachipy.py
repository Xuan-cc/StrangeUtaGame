# ------------------------------------------------------------------
# 自定义 sudachipy hook —— 仅收集 sudachidict_small
#
# 覆盖 pyinstaller-hooks-contrib 自带的 hook-sudachipy.py，
# 后者会把构建环境中安装的 sudachidict_core / sudachidict_full 也一并打入包中。
# noWinIME / mac 变体只需要 sudachidict_small 即可。
# ------------------------------------------------------------------

from PyInstaller.utils.hooks import collect_data_files, can_import_module, is_module_satisfies

datas = collect_data_files('sudachipy')
hiddenimports = []

if is_module_satisfies('sudachipy >= 0.6.8'):
    hiddenimports += [
        'sudachipy.config',
        'sudachipy.errors',
    ]

# 只收集 sudachidict_small，不收集 core / full
if can_import_module('sudachidict_small'):
    datas += collect_data_files('sudachidict_small')
    hiddenimports += ['sudachidict_small']

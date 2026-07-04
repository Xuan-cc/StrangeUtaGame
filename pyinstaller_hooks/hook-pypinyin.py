from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('pypinyin')
hiddenimports = collect_submodules('pypinyin')

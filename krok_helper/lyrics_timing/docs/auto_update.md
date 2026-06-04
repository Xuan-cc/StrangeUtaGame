# 自动更新（EXE + `_internal` 在线升级）

> 仅适用于 PyInstaller `--onedir` 打包后的桌面版本。
> 开发环境（`python main.py`）下，仍能弹出"有新版本"提示，但点击"立即更新"
> 会因找不到 Updater.exe 而提示用户去 GitHub 手动下载。

---

## 一、用户视角：他们是怎么收到更新提示的？

完全无需用户配置即可工作（默认源 = GitHub 官方）。

```
启动 StrangeUtaGame.exe
  │
  ▼
~2.5 秒后台异步调用 GitHub Release API
  │
  ├── 距上次成功检查 < 8 小时？  → 静默跳过（防抖）
  ├── 远端 latest 版本 ≤ 本地版本？ → 静默
  ├── 用户已"跳过此版本"？        → 静默
  └── 否则 → 弹窗显示版本号 + changelog（来自 GitHub Release body）
      │
      ├── [立即更新] → 主程序退出，由 Updater.exe 替换并自动重启
      ├── [稍后再说] → 关闭弹窗；下次启动满足条件时再提示
      └── [跳过此版本] → 写入 config，此版本不再提示
```

**手动入口**：设置 → 应用更新 → 立即检查更新（不受 8 小时防抖限制）。

**代理**：默认走 Windows 系统代理；用户可在 *设置 → 网络与代理* 切换为
"自动检测代理"（扫描本机常用代理端口）或"手动指定地址"。

---

## 二、维护者视角：更新日志写在哪里？

**写在 `CHANGELOG.md`**。文件已经准备好了，规则非常简单：

```markdown
## [0.3.3] - 2026-05-16

### 新增功能
- 启动时自动检查更新...

### 特性改变
- 版本号统一收敛到 __version__.py...

### 修复项目
- 修复...
```

* 标题严格使用 `## [X.Y.Z] - YYYY-MM-DD`，前后空一行；
* 子标题 `### 新增功能` / `### Cha特性改变nged` / `### 修复项目`（按 Keep a Changelog 风格）；
* 用 `scripts/release.py prepare 0.3.4` 自动注入空白段落，你只需要填条目内容。

**这段内容最终会原样发到 GitHub Release body，并展示在用户的更新弹窗里**，
所以建议用 Markdown 写得简洁可读：用户最关心"修了什么 / 加了什么"，没必要写
内部重构细节。

---

## 三、关键问答：什么时候 git push？

无论本地脚本还是 GitHub Actions，**都需要先 `git push`**。区别只是顺序：

| 流程            | 顺序                                                                 |
| --------------- | -------------------------------------------------------------------- |
| 本地脚本        | prepare → 改 CHANGELOG → **build** → commit → push → tag → push tag → 上 GitHub Web 创建 Release |
| GitHub Actions  | prepare → 改 CHANGELOG → commit → push → tag → **push tag**（触发 Actions 自动 build + Release）|

原因：

* **本地脚本**：build 在你本机跑，产物（zip + release_notes.md）也在本机；
  push 只是为了同步代码、打 tag 并发布。你可以"先 build 再 push"。
* **GitHub Actions**：runner 是 fresh checkout，**只能看到已经 push 到远端的提交**。
  所以你本地的所有改动（`__version__.py`、`CHANGELOG.md`、新模块）都必须先 push，
  然后再 push tag 触发工作流。

最常见的踩坑：改完 `__version__.py` 和 `CHANGELOG.md` 后忘了 commit / push，
就 `git tag SUGv0.3.3 && git push --tags`，结果 Actions 用的还是旧版本号或缺新文件。

> **首次启用 GitHub Actions 时**：本次的 `.github/workflows/release.yml`、
> `updater_app/`、`scripts/release.py`、`docs/auto_update.md`、`src/updater/`
> 等所有新增文件都必须先 push 到 main，**再** push tag。如果之前 tag 触发
> Actions 失败、然后你又删了 tag 重打，要确保 main 上的代码已经包含了对应
> 修复（比如下面要说的 UTF-8 编码修复）。

---

## 三-A、正常发布流程（一键脚本版）

### 0. 第一次发布前的准备（仅一次性）

```bat
:: 安装 PyInstaller（如果还没装）
pip install pyinstaller

:: 一次性构建 Updater.exe（之后只要 updater_app/ 没改就不用重打）
python updater_app\build_updater.py
```

产物：`updater_app/dist/Updater.exe`（~12–16 MB）。

### 1. 准备版本号与 changelog

```bat
python scripts\release.py prepare 0.3.3
```

脚本会做：

* 把 `src/strange_uta_game/__version__.py` 的 `__version__` 改为 `0.3.3`
* 在 `CHANGELOG.md` 顶部插入 `## [0.3.3] - <今天>` 占位段落

然后**手动打开 `CHANGELOG.md`** 把空白条目填完（这一步必须人工，AI 不知道你
真的改了什么）。

### 2. 一键构建

```bat
python scripts\release.py build
```

脚本会做：

1. 若 `updater_app/dist/Updater.exe` 不存在 → 调起 `updater_app/build_updater.py` 重新构建
2. 调起 `build.py`（你原有的脚本）做 PyInstaller `--onedir` 打包
3. `build.py` 末尾会自动把 `Updater.exe` 复制到 `dist/StrangeUtaGame/`
4. 把 `dist/StrangeUtaGame/` 整目录压成 `dist/StrangeUtaGame-v0.3.3.zip`
5. 从 CHANGELOG 抽出 `[0.3.3]` 段落写到 `dist/release_notes-v0.3.3.md`
6. 打印后续的 git / GitHub Web 操作指令

### 3. 提交并打 tag

```bat
git add -A
git commit -m "release v0.3.3"
git tag SUGv0.3.3
git push origin main --tags
```

### 4. 在 GitHub Web 创建 Release

打开 <https://github.com/Xuan-cc/StrangeUtaGame/releases/new>：

* **Tag** 选择 `SUGv0.3.3`（刚刚推上去的）
* **Release title** 写 `v0.3.3`
* **Description** 直接粘贴 `dist/release_notes-v0.3.3.md` 的全文
* **Attach binaries** 上传 `dist/StrangeUtaGame-v0.3.3.zip`
* 点 *Publish release*

完成。任意用户启动客户端时（且距上次检查 ≥ 8 小时）会看到新版本提示。

---

## 三-B、本地一键流程（不依赖 GitHub Actions）

完整时序如下（每条命令都可独立运行）：

```bat
:: A. 改版本号 + CHANGELOG 占位
python scripts\release.py prepare 0.3.3

:: B. 手动编辑 CHANGELOG.md 把 [0.3.3] 段落补完

:: C. 本地完成所有构建（产物在 dist/）
python scripts\release.py build

:: D. 同步到远端
git add -A
git commit -m "release v0.3.3"
git push origin main

:: E. 打 tag 并推送
git tag SUGv0.3.3
git push origin SUGv0.3.3

:: F. 上 GitHub Web 创建 Release（地址脚本会在 build 末尾打印）：
::    - Tag = SUGv0.3.3
::    - Title = v0.3.3
::    - Body = 粘 dist/release_notes-v0.3.3.md 全文
::    - Attach = dist/StrangeUtaGame-v0.3.3.zip
```

> 如果你启用了 GitHub Actions，**E 步会自动触发**远端构建。两边可能会重复构建，
> 但 Actions 创建 Release 时如果 tag 已存在 release，会失败。两种流程二选一。

---

## 四、GitHub Actions 全自动版（可选）

仓库已包含 `.github/workflows/release.yml`。开启方式：**只要你 push 一个
`SUGv*` 形式的 tag，工作流就会自动跑完整发布**：

```bat
:: 改完版本号与 CHANGELOG，本地不需要打包：
git add -A
git commit -m "release v0.3.3"
git tag SUGv0.3.3
git push origin main --tags
```

GitHub Actions 会在 Windows runner 上：

1. checkout 仓库
2. 装 Python 3.11 + 你的 `requirements.txt` + pyinstaller
3. 同步 `__version__.py` 与 tag 中的版本号一致
4. 跑 `updater_app/build_updater.py`
5. 跑 `build.py`
6. 打 zip
7. 从 CHANGELOG 抽 release notes
8. 用 `softprops/action-gh-release@v2` 创建 Release 并上传 zip

跑完约 8–12 分钟。**只要 tag 推上去就完事**，连"GitHub Web 创建 Release"那步都不用做。

### 它和本地脚本的差异

|                       | 本地脚本                  | GitHub Actions      |
| --------------------- | ------------------------- | ------------------- |
| 谁打包                | 你的电脑                  | GitHub 的 Windows runner |
| 是否需要本地装 PyInstaller | 是                       | 否                  |
| 是否需要手动 GitHub Web 操作 | 是                  | 否                  |
| 跨机器复现性          | 取决于本地 Python 环境    | 完全一致            |
| 速度                  | 取决于本机 SSD            | 8–12 分钟（含安装依赖）|

**推荐策略**：日常用本地脚本（快、能调试）；正式发布前过一遍 GitHub Actions
确认能稳定通过（避免"在我机器上能跑"）。

### 常见踩坑 ①：`GITHUB_TOKEN` 没有写权限（403 创建 Release 失败）

**症状**：所有构建步骤都成功，最后 `Create GitHub Release` 步骤崩在：

```
GitHub release failed with status: 403
{"message":"Resource not accessible by integration"}
Skip retry — your GitHub token/PAT does not have the required permission to create a release
```

**原因**：自 2023 年起 GitHub 新建仓库的默认 `GITHUB_TOKEN` 是「只读」，
`softprops/action-gh-release` 调 GitHub API 创建 Release / 上传 Asset 会被拒。

**修复**：工作流文件顶层声明 `permissions: contents: write`（仓库已经这么做了）：

```yaml
permissions:
  contents: write
```

或者：仓库 Settings → Actions → General → Workflow permissions → 选
"Read and write permissions"。两个二选一即可，不要都做。

### 常见踩坑 ②：Windows runner stdout 编码

GitHub 的 `windows-latest` runner Python 默认 stdout 编码是 **cp1252**
（Western European），无法编码中文字符。脚本里的 `print("开始打包 …")`
在本机能跑（中文 Windows 一般是 cp936），上 Actions 直接抛
`UnicodeEncodeError: 'charmap' codec can't encode characters`。

**仓库已经做了双保险**：

1. `.github/workflows/release.yml` 顶部 `env:` 设置了 `PYTHONIOENCODING=utf-8` /
   `PYTHONUTF8=1` —— Python 进程一律走 UTF-8。
2. 所有有中文输出的 Python 脚本（`build.py` / `updater_app/build_updater.py` /
   `updater_app/main.py` / `scripts/release.py`）顶部都加了
   `_force_utf8_stdio()`，即便没设 env 也能跑。

如果你后续新增脚本，**写中文 print 前先复制这个函数**：

```python
def _force_utf8_stdio() -> None:
    import sys
    for s in ("stdout", "stderr"):
        st = getattr(sys, s, None)
        if st is not None and hasattr(st, "reconfigure"):
            try:
                st.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

_force_utf8_stdio()
```

---

## 五、用户更新过程内部发生了什么？

```
StrangeUtaGame.exe (主进程, PyQt6)
    │
    │  ① 启动后 ~2.5s 异步拉取 GitHub Release "latest"
    │     - 按用户排序的源逐个尝试，前一个失败立刻降到下一个
    │     - 使用用户配置的代理
    │  ② 比较版本号；若有新版本 → 弹窗征求用户同意
    │  ③ 用户确认 → 启动 Updater.exe（命令行传参）并退出
    ▼
Updater.exe (独立控制台进程, 标准库 + requests, 12 MB)
    │
    │  ① 等待主进程 PID 退出（最多 30s）
    │  ② 按命令行候选 URL 逐个尝试下载 zip
    │  ③ （可选）SHA-256 校验
    │  ④ 解压到 %TEMP%
    │  ⑤ 备份现有 _internal/ → _internal.bak
    │       备份 StrangeUtaGame.exe → StrangeUtaGame.exe.bak
    │  ⑥ 复制新 _internal/ 与 StrangeUtaGame.exe 覆盖
    │     - 任何一步失败立即回滚备份
    │  ⑦ 启动新版本主程序
    │  ⑧ 清理临时与备份
```

**用户数据始终安全**：Updater 严格只动 `StrangeUtaGame.exe` 与 `_internal/`，
绝不触碰 `config.json` / `dictionary.json` / `network_dictionary.json` / `singers.json` / 任何
`.config_redirect` 指向的文件。

---

## 六、UI 组件位置

进入 *设置* 页面，从上往下依次能看到：

* 演奏控制 / 打轴设定 / Offset 校准 / 自动保存 / Auto Check / 读音词典 /
  界面设定 / 导出设定 / 快捷键 / 操作按钮
* **网络与代理（更新源）**  ← 新增
  - 代理模式（关闭 / 系统代理 / 自动检测 / 手动指定）
  - 手动代理地址
  - 当前生效代理 + 自动检测按钮 + 测试连通性按钮
* **应用更新**  ← 新增
  - 启动时检查更新（开关）
  - 启动检查间隔（防抖小时数，默认 8）
  - 更新源优先级（点击"编辑顺序"弹拖拽弹窗）
  - 立即检查更新（按钮）
* **关于**
  - 版本号 ← 从 `__version__` 动态读取
  - GitHub 仓库链接
  - 配置文件位置

---

## 七、配置项一览（`config.json` → `updater`）

```jsonc
{
  "updater": {
    "enabled": true,                          // 总开关，关掉后整个模块禁用
    "check_on_startup": true,                 // 启动时是否检查
    "min_check_interval_hours": 8,            // 启动检查的最小间隔
    "source_order": ["github", "ghproxy", "fastgit"],
    "proxy": {
      "mode": "system",                       // off / system / auto / manual
      "manual_url": ""
    },
    "skipped_version": "",                    // 用户点过"跳过此版本"的版本号
    "last_seen_version": "",                  // 最近一次发现的远端版本（debug 用）
    "last_check_at": 0                        // 上次成功拉取的 Unix 秒，用于防抖
  }
}
```

首次启动若 `config.json` 没有 `updater` 节点，进入设置面时会自动写入默认值。

---

## 八、三个更新源

| ID         | URL 模板                                                                |
| ---------- | ----------------------------------------------------------------------- |
| `github`   | `https://github.com/{O}/{R}/releases/download/{tag}/{file}`             |
| `ghproxy`  | `https://mirror.ghproxy.com/https://github.com/{O}/{R}/releases/download/{tag}/{file}` |
| `fastgit`  | `https://download.fastgit.org/{O}/{R}/releases/download/{tag}/{file}`   |

（`{O}` = `Xuan-cc`，`{R}` = `StrangeUtaGame`）

镜像 API（`api.github.com` 的代理）也走同一排序：

```
github   → https://api.github.com/repos/{O}/{R}/releases/latest
ghproxy  → https://mirror.ghproxy.com/https://api.github.com/repos/{O}/{R}/releases/latest
fastgit  → https://api.fastgit.org/repos/{O}/{R}/releases/latest
```

---

## 九、代理探测

* **关闭** — 强制不走代理
* **使用系统代理** — 读 Windows 注册表
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`
* **自动检测** — 系统代理优先，否则扫描以下常用本地端口：
  `7890, 7891, 7897, 17897, 10809, 10808, 1080, 1081, 2080, 8118, 8888, 8889,
  20171, 20172, 33210, 7070, 6152, 1087`
* **手动指定地址** — 支持
  - `http://127.0.0.1:7890`
  - `socks5://user:pass@host:1080`
  - `127.0.0.1:7890` （自动补 `http://`）

> 常见误会：**Clash Verge 默认不开 Windows 系统代理**（走 TUN 或浏览器扩展），
> 这种情况下"使用系统代理"会显示"未启用"。建议改成"自动检测"或"手动指定"。

---

## 十、失败处理矩阵

| 场景                      | 行为                                                       |
| ------------------------- | ---------------------------------------------------------- |
| 启动时全部源 API 失败     | 仅写日志，不弹窗；不阻塞主程序运行                            |
| 用户主动检查全部源失败    | 弹出 `UpdateCheckErrorDialog`，列出每个源的失败原因          |
| 防抖跳过                  | 静默，不弹任何东西                                          |
| 下载失败                  | 在源列表中接力到下一个；全部失败时 Updater 控制台显示并退出码 3 |
| SHA-256 不匹配            | Updater 退出码 4，不替换文件                                 |
| 写入新 `_internal` 失败   | 自动回滚备份（`_internal.bak` → `_internal`）                |
| 主程序 PID 未在 30s 内退出 | Updater 强制继续；锁定的文件会写失败 → 触发回滚              |

Updater 完整日志：`%TEMP%\StrangeUtaGameUpdater\updater.log`

---

## 十一、单元测试

```bat
python -m pytest tests\unit\updater -v
```

共 73+ 个测试，覆盖：
* 版本号解析 / 比较 / tag 前缀剥离
* 三源 URL 构造、用户排序归一化、API URL 构造
* Windows 系统代理字段解析、手动代理字符串解析
* GitHub Release JSON 解析、资产挑选、源 URL 覆写
* `LaunchPlan` ↔ Updater CLI 命令行格式互通
* `UpdaterSettings` 命名空间隔离、防抖逻辑、`ensure_persisted` 兜底

更新场景的端到端测试需要真实网络与已发布版本，建议手动 QA。

---

## 十二、已知限制 & 后续工作

* **仅 Windows**：`installer.launch_updater` 与 `proxy.read_system_proxy` 都依赖
  Win32 特性；macOS/Linux 下检查更新可执行，但"启动 Updater 替换"会失败。
* **仅 zip**：Updater 只识别 `.zip`。旧版（如 `SUGv0.3.2`）发布的 `.rar` 包
  不能直接被新 Updater 处理 —— 用户从旧版升级到第一个 zip 版本时需手动下载。
* **预发布通道**：`fetch_latest_release` 已预留 `include_prerelease` 参数，但
  UI 暂未暴露。需要时可在设置里加开关。
* **无 SHA-256 强制**：发布时若上传 `*.zip.sha256` 同名文件，Updater 会校验；
  否则跳过校验。可在 `scripts/release.py` 后续加入自动生成 sha256 的步骤。

"""``strange_uta_game.updater.installer`` 单元测试（仅命令行参数构造）。"""

from pathlib import Path

from strange_uta_game.updater.installer import LaunchPlan


class TestLaunchPlanCommandArgs:
    def _build(self, **overrides):
        defaults = dict(
            app_dir=Path("C:/x"),
            app_exe_name="StrangeUtaGame.exe",
            target_version="0.4.0",
            target_tag="SUGv0.4.0",
            asset_name="StrangeUtaGame-v0.4.0.zip",
            download_urls=[
                ("github", "https://github.com/a/b.zip"),
                ("ghproxy", "https://mirror.ghproxy.com/x.zip"),
            ],
            proxy_url="http://127.0.0.1:7890",
        )
        defaults.update(overrides)
        return LaunchPlan(**defaults)

    def test_minimal_args(self):
        plan = self._build()
        args = plan.command_args(Path("C:/temp/Updater.exe"), current_pid=1234)
        # 必填项
        assert args[0] == "C:\\temp\\Updater.exe"
        assert "--app-dir" in args
        assert "--app-exe" in args
        assert "--target-version" in args
        assert "--target-tag" in args
        assert "--asset-name" in args
        assert "--internal-name" in args
        assert "--pid" in args
        # PID 写对
        idx = args.index("--pid")
        assert args[idx + 1] == "1234"

    def test_url_serialization(self):
        plan = self._build()
        args = plan.command_args(Path("C:/u.exe"), current_pid=1)
        # 每个 url 单独一个 --url
        url_positions = [i for i, a in enumerate(args) if a == "--url"]
        assert len(url_positions) == 2
        # 值的格式必须是 "source|url"
        for pos in url_positions:
            value = args[pos + 1]
            assert "|" in value
            sid, url = value.split("|", 1)
            assert sid in ("github", "ghproxy", "fastgit")
            assert url.startswith("https://")

    def test_no_proxy_omits_flag(self):
        plan = self._build(proxy_url="")
        args = plan.command_args(Path("C:/u.exe"), current_pid=1)
        assert "--proxy" not in args

    def test_proxy_included(self):
        plan = self._build(proxy_url="http://127.0.0.1:7890")
        args = plan.command_args(Path("C:/u.exe"), current_pid=1)
        assert "--proxy" in args
        idx = args.index("--proxy")
        assert args[idx + 1] == "http://127.0.0.1:7890"

    def test_no_launch_after(self):
        plan = self._build(launch_after_update=False)
        args = plan.command_args(Path("C:/u.exe"), current_pid=1)
        assert "--no-launch" in args

    def test_default_launch_after(self):
        plan = self._build()
        args = plan.command_args(Path("C:/u.exe"), current_pid=1)
        assert "--no-launch" not in args

    def test_sha256_included(self):
        plan = self._build(expected_sha256="abc123")
        args = plan.command_args(Path("C:/u.exe"), current_pid=1)
        assert "--sha256" in args
        idx = args.index("--sha256")
        assert args[idx + 1] == "abc123"


class TestUpdaterAppArgsParser:
    """``updater_app.main.parse_args`` 与 LaunchPlan 的接口对齐性测试。"""

    def test_roundtrip(self):
        import sys
        sys.path.insert(0, r"E:\KaraMaker\StrangeUtaGame")
        from updater_app.main import parse_args

        plan = LaunchPlan(
            app_dir=Path("C:/x"),
            app_exe_name="StrangeUtaGame.exe",
            target_version="0.4.0",
            target_tag="SUGv0.4.0",
            asset_name="StrangeUtaGame-v0.4.0.zip",
            download_urls=[
                ("github", "https://a.com/x.zip"),
                ("fastgit", "https://b.com/x.zip"),
            ],
            proxy_url="http://127.0.0.1:7890",
            expected_sha256="deadbeef",
        )
        cmd = plan.command_args(Path("C:/u.exe"), current_pid=7777)
        # 模拟 Updater.exe 解析（跳过 argv[0]）
        parsed = parse_args(cmd[1:])
        assert parsed.target_version == "0.4.0"
        assert parsed.target_tag == "SUGv0.4.0"
        assert parsed.pid == 7777
        assert parsed.proxy_url == "http://127.0.0.1:7890"
        assert parsed.sha256 == "deadbeef"
        assert parsed.launch_after is True
        assert parsed.urls == [
            ("github", "https://a.com/x.zip"),
            ("fastgit", "https://b.com/x.zip"),
        ]

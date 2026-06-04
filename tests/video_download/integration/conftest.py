from __future__ import annotations

import pytest


# 集成测试用真实 URL。仅在 KROK_INTEGRATION=1 时启用。
# 注意：这些 URL 指向第三方平台上的视频，未来可能被删除 / 转私 / 区域受限。
# 若 Tier 2 集成测试突然集体失败，先确认 URL 是否仍然可访问。
YOUTUBE_PUBLIC_URL = "https://www.youtube.com/watch?v=khig9zYYEiM&list=RDkhig9zYYEiM&start_radio=1"
BILIBILI_PUBLIC_URL = "https://www.bilibili.com/video/BV1KHGo6BEGx/?spm_id_from=333.1007.tianma.1-2-2.click&vd_source=4612ec3b3ff0b076e1c04db4776f8e48"
BILIBILI_MULTIPART_URL = "https://www.bilibili.com/video/BV1duR9B7E6t?spm_id_from=333.788.videopod.sections&vd_source=4612ec3b3ff0b076e1c04db4776f8e48"


@pytest.fixture
def youtube_public_url() -> str:
    return YOUTUBE_PUBLIC_URL


@pytest.fixture
def bilibili_public_url() -> str:
    if BILIBILI_PUBLIC_URL.startswith("<"):
        pytest.skip("请先在 integration/conftest.py 填入公开 B 站短视频 URL")
    return BILIBILI_PUBLIC_URL


@pytest.fixture
def bilibili_multipart_url() -> str:
    if BILIBILI_MULTIPART_URL.startswith("<"):
        pytest.skip("请先在 integration/conftest.py 填入公开 B 站多 P 合集 URL")
    return BILIBILI_MULTIPART_URL

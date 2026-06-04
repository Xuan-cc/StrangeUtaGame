from __future__ import annotations

from krok_helper.audio_alignment import ENCODE_MODE_HARDWARE, ENCODE_MODE_SOFTWARE, _video_encoding_options


def test_hardware_encoding_uses_nvenc_for_h264_source() -> None:
    options = _video_encoding_options({"codec_name": "h264"}, ENCODE_MODE_HARDWARE)

    assert options[:2] == ["-c:v", "h264_nvenc"]


def test_hardware_encoding_defaults_to_h264_nvenc_for_unsupported_source_codec() -> None:
    options = _video_encoding_options({"codec_name": "vp9"}, ENCODE_MODE_HARDWARE)

    assert options[:2] == ["-c:v", "h264_nvenc"]
    assert "libx264" not in options


def test_software_encoding_keeps_cpu_encoder() -> None:
    options = _video_encoding_options({"codec_name": "h264"}, ENCODE_MODE_SOFTWARE)

    assert options[:2] == ["-c:v", "libx264"]

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .download_task import FormatOption


QUALITY_LABEL_PATTERN = re.compile(r"(\d{3,4})p(?:\d{2})?", flags=re.IGNORECASE)
FPS_LABEL_PATTERN = re.compile(r"(\d{2,3})帧")


def format_bytes(size: int | None) -> str:
    if size is None or size <= 0:
        return "-"

    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "-"


class FormatParser:
    def parse_formats(self, raw_formats: list[dict[str, Any]] | None) -> list[FormatOption]:
        formats = list(raw_formats or [])
        if not formats:
            return []

        best_audio = self._pick_best_audio(formats)
        candidates: dict[str, tuple[tuple[float, float, int], FormatOption]] = {}

        for item in formats:
            vcodec = str(item.get("vcodec") or "none")
            if vcodec == "none":
                continue

            height = int(item.get("height") or 0)
            width = int(item.get("width") or 0)
            if height <= 0 and width <= 0:
                continue

            resolution = self._build_resolution_label(item, width, height)
            acodec = str(item.get("acodec") or "none")
            ext = str(item.get("ext") or "").lower()
            format_id = str(item.get("format_id") or "")
            if not format_id:
                continue

            filesize = self._coalesce_size(item)
            requires_merge = acodec == "none" and best_audio is not None
            if requires_merge:
                audio_id = str(best_audio.get("format_id") or "")
                if not audio_id:
                    continue
                download_format = f"{format_id}+{audio_id}"
                filesize = (filesize or 0) + (self._coalesce_size(best_audio) or 0) or None
                audio_codec = str(best_audio.get("acodec") or "unknown")
                format_label = f"{ext.upper() or '视频'} + 音频"
            else:
                download_format = format_id
                audio_codec = acodec
                format_label = ext.upper() or "默认"

            fps = self._coalesce_fps(item)
            note = self._clean_note(str(item.get("format_note") or ""))
            option = FormatOption(
                option_id=f"{format_id}:{resolution}",
                download_format=download_format,
                format_label=format_label,
                resolution=resolution,
                video_codec=self._normalize_codec(vcodec),
                audio_codec=self._normalize_codec(audio_codec),
                filesize=filesize,
                ext=ext,
                note=note,
                height=height,
                width=width,
                requires_merge=requires_merge,
            )

            score = (
                float(item.get("tbr") or item.get("vbr") or 0),
                fps,
                filesize or 0,
            )
            variant_key = self._build_variant_key(item, resolution, note, fps)
            current = candidates.get(variant_key)
            if current is None or score >= current[0]:
                candidates[variant_key] = (score, option)

        ordered = sorted(
            (entry[1] for entry in candidates.values()),
            key=lambda option: (
                self._resolution_rank(option),
                self._resolution_fps_rank(option),
                self._note_priority(option.note),
                option.filesize or 0,
            ),
            reverse=True,
        )
        if not ordered:
            return []

        recommended = self.build_recommended_option(ordered)
        result = [recommended]
        for option in ordered:
            if option.resolution == recommended.resolution and option.download_format == recommended.download_format:
                continue
            result.append(option)
        return result

    def build_recommended_option(self, options: list[FormatOption]) -> FormatOption:
        if not options:
            raise ValueError("formats cannot be empty")

        best = options[0]
        return replace(
            best,
            option_id=f"recommended:{best.option_id}",
            format_label="最佳质量",
            is_recommended=True,
        )

    def _pick_best_audio(self, formats: list[dict[str, Any]]) -> dict[str, Any] | None:
        audio_only = [
            item
            for item in formats
            if str(item.get("vcodec") or "none") == "none" and str(item.get("acodec") or "none") != "none"
        ]
        if not audio_only:
            return None
        return max(
            audio_only,
            key=lambda item: (
                float(item.get("abr") or 0),
                float(item.get("tbr") or 0),
                self._coalesce_size(item) or 0,
            ),
        )

    def _build_resolution_label(self, item: dict[str, Any], width: int, height: int) -> str:
        base_label = self._extract_quality_label(item)
        if not base_label:
            if width > 0 and height > 0:
                base_label = f"{min(width, height)}p"
            elif height > 0:
                base_label = f"{height}p"
            elif width > 0:
                base_label = f"{width}px"
            else:
                base_label = "未知"

        suffixes: list[str] = []
        fps = self._coalesce_fps(item)
        if fps >= 49 and "60" not in base_label and "50" not in base_label:
            suffixes.append(f"{int(round(fps))}帧")

        note = self._clean_note(str(item.get("format_note") or ""))
        if note and not self._note_redundant(note, base_label):
            suffixes.append(note)

        dynamic_range = str(item.get("dynamic_range") or "").strip().upper()
        if dynamic_range and dynamic_range != "SDR" and dynamic_range not in suffixes:
            suffixes.append(dynamic_range)

        return " ".join([base_label, *suffixes]).strip()

    def _extract_quality_label(self, item: dict[str, Any]) -> str:
        quality_label = str(item.get("quality_label") or "").strip()
        if quality_label:
            return quality_label

        for text in (
            str(item.get("format_note") or "").strip(),
            str(item.get("format") or "").strip(),
            str(item.get("resolution") or "").strip(),
        ):
            match = QUALITY_LABEL_PATTERN.search(text)
            if match:
                return f"{match.group(1)}p"

        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        if width > 0 and height > 0:
            return f"{min(width, height)}p"
        return ""

    def _build_variant_key(self, item: dict[str, Any], resolution: str, note: str, fps: float) -> str:
        dynamic_range = str(item.get("dynamic_range") or "").strip().upper()
        parts = [resolution]
        if fps >= 49 and "帧" not in resolution:
            parts.append(f"{int(round(fps))}fps")
        if note and not self._note_redundant(note, resolution):
            parts.append(note.lower())
        if dynamic_range and dynamic_range != "SDR":
            parts.append(dynamic_range)
        return "|".join(parts)

    def _note_redundant(self, note: str, resolution: str) -> bool:
        note_lower = note.lower().strip()
        resolution_lower = resolution.lower().strip()
        if not note_lower:
            return True
        if note_lower in resolution_lower or resolution_lower in note_lower:
            return True
        simplified_note = re.sub(r"[\s_/-]+", "", note_lower)
        simplified_resolution = re.sub(r"[\s_/-]+", "", resolution_lower)
        return simplified_note == simplified_resolution

    def _clean_note(self, note: str) -> str:
        if not note:
            return ""
        cleaned = note.strip()
        cleaned = QUALITY_LABEL_PATTERN.sub("", cleaned)
        cleaned = re.sub(r"\bDASH\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bvideo only\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\baudio only\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bHLS\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/")
        return cleaned

    def _coalesce_fps(self, item: dict[str, Any]) -> float:
        value = item.get("fps")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 0.0

    def _resolution_rank(self, option: FormatOption) -> int:
        match = QUALITY_LABEL_PATTERN.search(option.resolution)
        if match:
            return int(match.group(1))
        return min(value for value in (option.height, option.width) if value > 0) if (option.height > 0 or option.width > 0) else 0

    def _resolution_fps_rank(self, option: FormatOption) -> int:
        match = FPS_LABEL_PATTERN.search(option.resolution)
        if match:
            return int(match.group(1))
        return 0

    def _note_priority(self, note: str) -> int:
        lower = (note or "").lower()
        score = 0
        for keyword, weight in (
            ("premium", 5),
            ("hdr", 4),
            ("dolby", 4),
            ("高码率", 3),
            ("高帧率", 3),
            ("会员", 2),
        ):
            if keyword in lower:
                score += weight
        return score

    def _normalize_codec(self, codec: str) -> str:
        if not codec or codec == "none":
            return "-"
        return codec.split(".")[0]

    def _coalesce_size(self, item: dict[str, Any]) -> int | None:
        for key in ("filesize", "filesize_approx"):
            value = item.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return None

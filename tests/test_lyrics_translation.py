from __future__ import annotations

import base64
import json

from krok_helper.lyrics import LyricsSearchCandidate, QqMusicLyricsProvider, _convert_krc_text


def test_convert_krc_extracts_language_translation() -> None:
    language_payload = {
        "content": [
            {"type": 0, "language": 0, "lyricContent": [["he ", "llo"], ["world"]]},
            {"type": 1, "language": 0, "lyricContent": [["你好"], ["世界"]]},
        ],
        "version": 1,
    }
    language_tag = base64.b64encode(json.dumps(language_payload).encode("utf-8")).decode("ascii")
    raw_krc = "\n".join(
        [
            f"[language:{language_tag}]",
            "[1000,2000]<0,500,0>Hello",
            "[3000,2000]<0,500,0>World",
        ]
    )

    _line_lyrics, _verbatim_lyrics, _plain_lyrics, translation_lyrics = _convert_krc_text(raw_krc)

    assert translation_lyrics.splitlines() == ["[00:01.00]你好", "[00:03.00]世界"]


def test_qq_fetch_lyrics_falls_back_to_play_lyric_info_translation(monkeypatch) -> None:
    encoded_translation = base64.b64encode(
        "[00:10.10]//\n[00:21.30]爱恨交织 在心里翻腾".encode("utf-8")
    ).decode("ascii")
    seen_payloads: list[dict] = []

    def fake_load_json(request):
        if "fcg_query_lyric_new.fcg" in request.full_url:
            return {"lyric": "[00:01.00]AIZO - King Gnu", "trans": ""}
        payload = json.loads(request.data.decode("utf-8"))
        seen_payloads.append(payload)
        return {"req": {"data": {"trans": encoded_translation}}}

    monkeypatch.setattr("krok_helper.lyrics._load_json_from_request", fake_load_json)
    candidate = LyricsSearchCandidate(
        provider_id="qm",
        provider_name="QQ音乐",
        track_id="632959788",
        title="AIZO",
        artist="King Gnu",
        album="AIZO",
        duration_seconds=215,
        provider_payload={"id": "632959788", "mid": "004Q1MO7275WXv"},
    )

    loaded = QqMusicLyricsProvider().fetch_lyrics(candidate)

    assert loaded.translation_lyrics == "[00:21.30]爱恨交织 在心里翻腾"
    assert seen_payloads[0]["req"]["param"] == {
        "songMID": "004Q1MO7275WXv",
        "songID": 632959788,
        "qrc": 0,
        "trans": 1,
        "roma": 0,
    }

"""Characterization tests; no real YouTube or paid API calls."""

from unittest.mock import MagicMock

import pytest

from app.services import youtube

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}&t=30s",
        f"https://youtu.be/{VIDEO_ID}?t=30",
    ],
)
def test_additional_supported_url_shapes(url):
    assert youtube.extract_video_id(url) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    ["", "https://www.youtube.com/", "https://youtu.be/short", "https://www.youtube.com/watch?v="],
)
def test_missing_or_short_video_id_is_rejected(url):
    with pytest.raises(ValueError, match="YouTube"):
        youtube.extract_video_id(url)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("WEBVTT\nKind: captions\nLanguage: ru\n\n", ""),
        (
            "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n<c>Привет</c> мир\n"
            "2\n00:00:02.000 --> 00:00:03.000\nПривет мир\nНовая строка\n",
            "Привет мир Новая строка",
        ),
        ("Первая\nВторая\nПервая", "Первая Вторая Первая"),
    ],
)
def test_clean_vtt_characterization(raw, expected):
    assert youtube._clean_vtt(raw) == expected


def test_parse_vtt_keeps_real_start_times():
    """Раньше «часы» не попадали в группу regex, и все реплики получали 00:00."""
    raw = ("WEBVTT\n\n"
           "00:00:01.000 --> 00:00:04.000\nПервая\n\n"
           "01:23:45.500 --> 01:23:47.000\nПоздняя\n\n"
           "00:02:03,250 --> 00:02:04,000\nС запятой\n")
    cues = youtube._parse_vtt(raw)
    assert [(c.start, c.text) for c in cues] == [(1.0, "Первая"), (5025.5, "Поздняя"), (123.25, "С запятой")]


def test_parse_vtt_drops_markup_and_accumulating_duplicates():
    raw = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c>Привет</c>\n\n"
           "00:00:02.000 --> 00:00:03.000\nПривет мир\n")
    cues = youtube._parse_vtt(raw)
    assert [c.text for c in cues] == ["Привет", "Привет мир"]


def test_vtt_seconds_tolerates_garbage():
    assert youtube._vtt_seconds("не таймкод") == 0.0


@pytest.fixture()
def subtitle_env(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube.settings, "data_dir", tmp_path)
    monkeypatch.setattr(youtube.settings, "subtitle_langs", "ru,en")
    monkeypatch.setattr(youtube.settings, "yt_cookies_file", None)
    monkeypatch.setattr(youtube.settings, "yt_proxy", None)
    factory = MagicMock(name="YoutubeDL")
    downloader = factory.return_value.__enter__.return_value
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    return factory, downloader


def test_subtitles_missing_returns_none(subtitle_env):
    factory, downloader = subtitle_env
    assert youtube.download_subtitles(URL, VIDEO_ID) is None
    downloader.download.assert_called_once_with([URL])
    opts = factory.call_args.args[0]
    assert opts["skip_download"] is True
    assert opts["noplaylist"] is True
    assert opts["writesubtitles"] is True
    assert opts["writeautomaticsub"] is True
    assert opts["subtitleslangs"] == ["ru", "en"]


def test_subtitles_download_error_returns_none(subtitle_env):
    _, downloader = subtitle_env
    downloader.download.side_effect = youtube.yt_dlp.utils.DownloadError("Subtitles unavailable")
    assert youtube.download_subtitles(URL, VIDEO_ID) is None


def test_subtitles_are_cleaned_and_consumed_file_removed(subtitle_env):
    _, downloader = subtitle_env
    subtitle = youtube.settings.media_dir / f"{VIDEO_ID}.ru.vtt"
    text = "Это достаточно длинная строка субтитров для проверки обработки и удаления файла."

    def write_subtitle(_urls):
        subtitle.write_text(f"WEBVTT\n\n00:00:00.000 --> 00:00:04.000\n{text}\n", encoding="utf-8")

    downloader.download.side_effect = write_subtitle
    assert youtube.download_subtitles(URL, VIDEO_ID) == (text, "ru")
    assert not subtitle.exists()


def test_short_subtitles_fall_back_to_next_language(subtitle_env):
    _, downloader = subtitle_env
    media = youtube.settings.media_dir
    russian = media / f"{VIDEO_ID}.ru.vtt"
    english = media / f"{VIDEO_ID}.en.vtt"
    text = "This subtitle contains enough text to pass the current minimum length check."

    def write_subtitles(_urls):
        russian.write_text("WEBVTT\n\nКоротко\n", encoding="utf-8")
        english.write_text(f"WEBVTT\n\n{text}\n", encoding="utf-8")

    downloader.download.side_effect = write_subtitles
    assert youtube.download_subtitles(URL, VIDEO_ID) == (text, "en")
    assert not russian.exists()
    assert not english.exists()

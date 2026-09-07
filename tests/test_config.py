from __future__ import annotations

from podcast_downloader.config import load_config


def test_load_config_strips_accidental_wrapping_quotes(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text('input: "\'/Users/example/shows.xlsx\'"\noperator: "\\"Trey\\""\n', encoding="utf-8")

    config = load_config(config_path)

    assert config.input == "/Users/example/shows.xlsx"
    assert config.operator == "Trey"

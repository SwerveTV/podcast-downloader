from __future__ import annotations

from pathlib import Path

from podcast_downloader.cli import main


def test_run_uses_input_and_operator_from_config(fixtures_dir: Path, tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                f'input: "{fixtures_dir / "shows.csv"}"',
                'operator: "Trey"',
                f'output_root: "{tmp_path / "Podcast_Ingest"}"',
                'progress: "none"',
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["--config", str(config), "run", "--dry-run"])

    assert exit_code == 0
    assert "Alpha Show" in capsys.readouterr().out


def test_cli_input_and_operator_override_config(fixtures_dir: Path, tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                f'input: "{fixtures_dir / "missing.csv"}"',
                'operator: "Nobody"',
                f'output_root: "{tmp_path / "Podcast_Ingest"}"',
                'progress: "none"',
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config),
            "run",
            "--dry-run",
            "--input",
            str(fixtures_dir / "shows.csv"),
            "--operator",
            "Trey",
        ]
    )

    assert exit_code == 0
    assert "Beta Show" in capsys.readouterr().out


def test_validate_uses_input_from_config(fixtures_dir: Path, tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(f'input: "{fixtures_dir / "shows.csv"}"\n', encoding="utf-8")

    exit_code = main(["--config", str(config), "validate"])

    assert exit_code == 0
    assert '"valid_playlist_jobs": 3' in capsys.readouterr().out

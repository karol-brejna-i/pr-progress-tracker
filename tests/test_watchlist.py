from __future__ import annotations

import pytest

from pr_tracker.watchlist import ConfigError, load_config, load_watchlist, parse_pr_url


class TestParsePrUrl:
    def test_plain_url(self):
        ref = parse_pr_url("https://github.com/pytorch/ao/pull/4893")
        assert (ref.owner, ref.repo, ref.number) == ("pytorch", "ao", 4893)
        assert ref.key == "pytorch/ao#4893"
        assert ref.slug == "pytorch__ao__4893"

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/pytorch/ao/pull/4893/",
            "https://github.com/pytorch/ao/pull/4893/files",
            "https://github.com/pytorch/ao/pull/4893#issuecomment-1",
            "https://github.com/pytorch/ao/pull/4893?w=1",
            "  https://github.com/pytorch/ao/pull/4893  ",
        ],
    )
    def test_tolerates_pasted_variants(self, url):
        assert parse_pr_url(url).number == 4893

    def test_dots_and_dashes_in_names(self):
        ref = parse_pr_url("https://github.com/my-org/my.repo_x/pull/7")
        assert (ref.owner, ref.repo) == ("my-org", "my.repo_x")

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/pytorch/ao/issues/4893",
            "https://github.com/pytorch/ao/pull/notanumber",
            "http://github.com/pytorch/ao/pull/1",
            "https://gitlab.com/pytorch/ao/pull/1",
            "pytorch/ao#4893",
            "",
        ],
    )
    def test_rejects_junk_loudly(self, url):
        with pytest.raises(ConfigError):
            parse_pr_url(url)


class TestLoadWatchlist:
    def test_skips_comments_and_blanks(self, tmp_path):
        p = tmp_path / "prs.txt"
        p.write_text(
            "# a comment\n"
            "\n"
            "https://github.com/pytorch/ao/pull/1\n"
            "   \n"
            "https://github.com/pytorch/ao/pull/2\n"
        )
        assert [r.number for r in load_watchlist(p)] == [1, 2]

    def test_collapses_duplicates_keeping_first_order(self, tmp_path):
        p = tmp_path / "prs.txt"
        p.write_text(
            "https://github.com/pytorch/ao/pull/2\n"
            "https://github.com/pytorch/ao/pull/1\n"
            "https://github.com/pytorch/ao/pull/2/files\n"
        )
        assert [r.number for r in load_watchlist(p)] == [2, 1]

    def test_one_bad_line_fails_the_run(self, tmp_path):
        p = tmp_path / "prs.txt"
        p.write_text("https://github.com/pytorch/ao/pull/1\nnonsense\n")
        with pytest.raises(ConfigError):
            load_watchlist(p)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            load_watchlist(tmp_path / "nope.txt")


class TestLoadConfig:
    def _write(self, d, internal="", external="", settings=None):
        (d / "internal-reviewers.txt").write_text(internal)
        (d / "external-reviewers.txt").write_text(external)
        if settings is not None:
            (d / "settings.json").write_text(settings)

    def test_normalizes_logins(self, tmp_path):
        self._write(tmp_path, internal="@VKuzo\n# note\nandrewor14\n", external="liangan1\n")
        cfg = load_config(tmp_path)
        assert cfg.internal_reviewers == {"vkuzo", "andrewor14"}
        assert cfg.classify("VKUZO") == "internal"
        assert cfg.classify("@liangan1") == "external"
        assert cfg.classify("stranger") == "other"
        assert cfg.classify(None) == "other"

    def test_overlapping_lists_is_fatal(self, tmp_path):
        self._write(tmp_path, internal="alice\nbob\n", external="BOB\n")
        with pytest.raises(ConfigError, match="BOTH"):
            load_config(tmp_path)

    def test_settings_defaults_when_absent(self, tmp_path):
        self._write(tmp_path)
        cfg = load_config(tmp_path)
        assert (cfg.stale_review_hours, cfg.stale_merge_hours) == (48, 72)

    def test_settings_override(self, tmp_path):
        self._write(tmp_path, settings='{"stale_review_hours": 12, "percentiles": [50, 95]}')
        cfg = load_config(tmp_path)
        assert cfg.stale_review_hours == 12
        assert cfg.percentiles == (50, 95)

    def test_bad_settings_json_is_fatal(self, tmp_path):
        self._write(tmp_path, settings="{not json")
        with pytest.raises(ConfigError, match="not valid JSON"):
            load_config(tmp_path)

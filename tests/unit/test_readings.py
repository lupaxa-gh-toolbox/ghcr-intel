from datetime import UTC, datetime

from lupaxa.ghcr_intel.sync.readings import read_versions, stale_result, status_text


def _stamp(instant: float) -> str:
    return datetime.fromtimestamp(instant, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_read_versions_keeps_the_newest_tagged_digest_and_truncates() -> None:
    body = [
        {
            "name": "sha256:new",
            "created_at": "2026-11-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "name": "sha256:tagged",
            "created_at": "2026-10-01T00:00:00Z",
            "metadata": {"container": {"tags": ["1.0", "latest"]}},
        },
    ]
    page = read_versions(body, limit=2)
    assert page.truncated is True
    assert page.fetched == 2
    assert page.newest_tag == "latest"
    assert page.digest == "sha256:tagged"
    assert page.tags == ("1.0", "latest")


def test_untagged_page_uses_the_newest_name() -> None:
    body = [
        {
            "name": "sha256:only",
            "created_at": "2026-11-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
    ]
    page = read_versions(body, limit=100)
    assert page.truncated is False
    assert page.newest_tag == ""
    assert page.digest == "sha256:only"


def test_stale_thresholds_and_a_disabled_check() -> None:
    old = "2020-01-01T00:00:00Z"
    now = 1_763_596_800.0  # 2026-11-20T00:00:00Z
    failed = stale_result(updated=old, newest_tag_at=old, now=now, package_days=180, tag_days=90, enabled=True)
    assert failed.failed is True
    fresh = stale_result(updated=old, newest_tag_at=old, now=now, package_days=180, tag_days=90, enabled=False)
    assert fresh.failed is False


def test_status_sentences() -> None:
    assert status_text(metadata_status=200, metadata_error=None, versions_error=None, stale=None, metadata_enabled=True) == ("Current", "Metadata was read.")
    short, sentence = status_text(metadata_status=404, metadata_error="missing", versions_error=None, stale=None, metadata_enabled=True)
    assert short == "Unavailable"
    assert sentence == "GitHub could not find this image."
    assert "Unavailable" not in sentence


def test_forbidden_error_and_metadata_disabled() -> None:
    assert status_text(metadata_status=403, metadata_error="The token cannot read this image.", versions_error=None, stale=None, metadata_enabled=True) == (
        "Forbidden",
        "The token cannot read this image.",
    )
    assert status_text(metadata_status=200, metadata_error=None, versions_error="versions failed", stale=None, metadata_enabled=True) == ("Error", "versions failed")
    assert status_text(metadata_status=0, metadata_error=None, versions_error=None, stale=None, metadata_enabled=False) == ("Current", "Metadata was read.")


def test_latest_wins_and_the_chosen_version_keeps_its_created_at() -> None:
    body = [
        {
            "name": "sha256:new",
            "created_at": "2026-11-02T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "name": "sha256:tagged",
            "created_at": "2026-10-01T00:00:00Z",
            "metadata": {"container": {"tags": ["1.0", "latest", "2.0"]}},
        },
    ]
    page = read_versions(body, limit=10)
    assert page.truncated is False
    assert page.newest_tag == "latest"
    assert page.digest == "sha256:tagged"
    assert page.newest_tag_at == "2026-10-01T00:00:00Z"
    assert page.tags == ("1.0", "2.0", "latest")


def test_last_tag_is_used_when_latest_is_absent() -> None:
    body = [
        {
            "name": "sha256:a",
            "created_at": "2026-11-02T00:00:00Z",
            "metadata": {"container": {"tags": ["zeta", "alpha"]}},
        },
        {
            "name": "sha256:b",
            "created_at": "2026-11-01T00:00:00Z",
            "metadata": {"container": {"tags": ["alpha", "mid"]}},
        },
    ]
    page = read_versions(body, limit=10)
    assert page.newest_tag == "alpha"
    assert page.digest == "sha256:a"
    assert page.newest_tag_at == "2026-11-02T00:00:00Z"
    assert page.tags == ("alpha", "mid", "zeta")


def test_limit_drops_later_versions() -> None:
    body = [
        {
            "name": "sha256:a",
            "created_at": "2026-11-02T00:00:00Z",
            "metadata": {"container": {"tags": ["2.0"]}},
        },
        {
            "name": "sha256:b",
            "created_at": "2026-11-01T00:00:00Z",
            "metadata": {"container": {"tags": ["1.0", "latest"]}},
        },
    ]
    page = read_versions(body, limit=1)
    assert page.fetched == 1
    assert page.truncated is True
    assert page.tags == ("2.0",)
    assert page.newest_tag == "2.0"
    assert page.digest == "sha256:a"
    assert page.newest_tag_at == "2026-11-02T00:00:00Z"


def test_untagged_page_keeps_the_first_created_at() -> None:
    body = [
        {"name": "sha256:only", "created_at": "2026-11-01T00:00:00Z"},
    ]
    page = read_versions(body, limit=100)
    assert page.newest_tag == ""
    assert page.digest == "sha256:only"
    assert page.newest_tag_at == "2026-11-01T00:00:00Z"
    assert page.tags == ()


def test_missing_timestamp_does_not_fail_that_half() -> None:
    now = 1_700_000_000.0
    package_only = stale_result(updated=_stamp(now - 180 * 86400), newest_tag_at="", now=now, package_days=180, tag_days=90, enabled=True)
    assert package_only.failed is True
    assert package_only.package_days_old == 180
    assert package_only.tag_days_old is None
    tag_only = stale_result(updated="", newest_tag_at=_stamp(now - 90 * 86400), now=now, package_days=180, tag_days=90, enabled=True)
    assert tag_only.failed is True
    assert tag_only.package_days_old is None
    assert tag_only.tag_days_old == 90
    neither = stale_result(updated="", newest_tag_at="", now=now, package_days=180, tag_days=90, enabled=True)
    assert neither.failed is False
    assert neither.package_days_old is None
    assert neither.tag_days_old is None


def test_age_just_under_the_threshold_is_fresh() -> None:
    now = 1_700_000_000.0
    under = stale_result(updated=_stamp(now - 180 * 86400 + 1), newest_tag_at=_stamp(now - 90 * 86400 + 1), now=now, package_days=180, tag_days=90, enabled=True)
    assert under.failed is False
    assert under.package_days_old == 179
    assert under.tag_days_old == 89


def test_disabled_check_records_ages_without_failing() -> None:
    now = 1_700_000_000.0
    fresh = stale_result(updated=_stamp(now - 200 * 86400), newest_tag_at=_stamp(now - 100 * 86400), now=now, package_days=180, tag_days=90, enabled=False)
    assert fresh.failed is False
    assert fresh.package_days_old == 200
    assert fresh.tag_days_old == 100


def test_stale_sentence_mentions_ages_without_the_short_word() -> None:
    now = 1_700_000_000.0
    stale = stale_result(updated=_stamp(now - 200 * 86400), newest_tag_at=_stamp(now - 100 * 86400), now=now, package_days=180, tag_days=90, enabled=True)
    short, sentence = status_text(metadata_status=200, metadata_error=None, versions_error=None, stale=stale, metadata_enabled=True)
    assert short == "Stale"
    assert sentence == ("The package was last updated 200 days ago and the newest tag is 100 days old.")
    assert "Stale" not in sentence
    package_only = stale_result(updated=_stamp(now - 200 * 86400), newest_tag_at="", now=now, package_days=180, tag_days=90, enabled=True)
    assert status_text(metadata_status=200, metadata_error=None, versions_error=None, stale=package_only, metadata_enabled=True) == ("Stale", "The package was last updated 200 days ago.")
    tag_only = stale_result(updated="", newest_tag_at=_stamp(now - 100 * 86400), now=now, package_days=180, tag_days=90, enabled=True)
    assert status_text(metadata_status=200, metadata_error=None, versions_error=None, stale=tag_only, metadata_enabled=True) == ("Stale", "The newest tag is 100 days old.")


def test_fresh_stale_check_uses_the_window_sentence() -> None:
    now = 1_700_000_000.0
    fresh = stale_result(updated=_stamp(now - 10 * 86400), newest_tag_at=_stamp(now - 5 * 86400), now=now, package_days=180, tag_days=90, enabled=True)
    assert fresh.failed is False
    assert status_text(metadata_status=200, metadata_error=None, versions_error=None, stale=fresh, metadata_enabled=True) == ("Current", "The package and its newest tag are inside the stale window.")


def test_other_statuses_and_a_versions_error_on_success() -> None:
    assert status_text(metadata_status=401, metadata_error=None, versions_error="ignored", stale=None, metadata_enabled=True) == ("Forbidden", "The token cannot read this image.")
    assert status_text(metadata_status=500, metadata_error="boom", versions_error=None, stale=None, metadata_enabled=True) == ("Error", "boom")
    assert status_text(metadata_status=404, metadata_error="missing", versions_error="versions failed", stale=None, metadata_enabled=False) == ("Unavailable", "GitHub could not find this image.")
    assert status_text(metadata_status=0, metadata_error=None, versions_error="versions failed", stale=None, metadata_enabled=False) == ("Error", "versions failed")

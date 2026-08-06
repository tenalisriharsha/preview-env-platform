from datetime import datetime, timedelta, timezone

import pytest

from previewctl.cleanup import (
    PreviewEnvironment,
    expired_environments,
    parse_namespace_list,
    parse_ttl,
)

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


class TestParseTTL:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30s", timedelta(seconds=30)),
            ("30m", timedelta(minutes=30)),
            ("24h", timedelta(hours=24)),
            ("7d", timedelta(days=7)),
            (" 24H ", timedelta(hours=24)),
        ],
    )
    def test_valid(self, text, expected):
        assert parse_ttl(text) == expected

    @pytest.mark.parametrize("bad", ["", "24", "h", "1w", "-5h", "1.5h", "abc"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            parse_ttl(bad)


def env(name, age, pr=None):
    return PreviewEnvironment(
        namespace=name, created_at=NOW - age, pr=pr
    )


class TestExpiredEnvironments:
    def test_only_environments_older_than_ttl_expire(self):
        envs = [
            env("preview-a-pr-1", timedelta(hours=25), pr=1),
            env("preview-a-pr-2", timedelta(hours=2), pr=2),
            env("preview-a-pr-3", timedelta(days=3), pr=3),
        ]
        expired = expired_environments(envs, timedelta(hours=24), NOW)
        assert [e.namespace for e in expired] == ["preview-a-pr-1", "preview-a-pr-3"]

    def test_exactly_at_ttl_counts_as_expired(self):
        envs = [env("preview-a-pr-1", timedelta(hours=24))]
        assert expired_environments(envs, timedelta(hours=24), NOW)

    def test_naive_created_at_is_treated_as_utc(self):
        naive = PreviewEnvironment(
            namespace="preview-a-pr-1",
            created_at=datetime(2026, 8, 5, 12, 0, 0),  # naive, 24h before NOW
        )
        assert naive.is_expired(timedelta(hours=23), NOW)

    def test_rejects_non_positive_ttl(self):
        with pytest.raises(ValueError):
            expired_environments([], timedelta(0), NOW)


class TestParseNamespaceList:
    def test_parses_kubectl_json(self):
        raw = b"""
        {"items": [
          {"metadata": {
             "name": "preview-app-pr-12",
             "creationTimestamp": "2026-08-05T10:00:00Z",
             "labels": {"preview.env/platform": "true", "preview.env/pr": "12"}}},
          {"metadata": {
             "name": "preview-app-pr-13",
             "creationTimestamp": "2026-08-06T09:30:00Z",
             "labels": {"preview.env/platform": "true"}}}
        ]}
        """
        envs = parse_namespace_list(raw)
        assert [e.namespace for e in envs] == ["preview-app-pr-12", "preview-app-pr-13"]
        assert envs[0].pr == 12
        assert envs[1].pr is None
        assert envs[0].created_at.tzinfo is not None

    def test_skips_items_without_creation_timestamp(self):
        raw = '{"items": [{"metadata": {"name": "pending-ns"}}]}'
        assert parse_namespace_list(raw) == []

    def test_empty_list(self):
        assert parse_namespace_list('{"items": []}') == []

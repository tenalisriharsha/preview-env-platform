import pytest

from previewctl.cli import main


class TestNameCommand:
    def test_prints_namespace(self, capsys):
        assert main(["name", "--repo", "octocat/hello-world", "--pr", "12"]) == 0
        assert capsys.readouterr().out.strip() == "preview-hello-world-pr-12"


class TestRenderCommand:
    def test_writes_overlay_and_reports(self, tmp_path, capsys):
        out = tmp_path / "overlay"
        rc = main(
            [
                "render",
                "--repo", "octocat/hello-world",
                "--pr", "12",
                "--image", "app",
                "--out", str(out),
            ]
        )
        assert rc == 0
        text = (out / "kustomization.yaml").read_text()
        assert "namespace: preview-hello-world-pr-12" in text
        assert "newTag: pr-12" in text
        printed = capsys.readouterr().out
        assert "host=pr-12.preview.example.com" in printed

    def test_custom_tag_and_domain(self, tmp_path):
        out = tmp_path / "overlay"
        main(
            [
                "render",
                "--repo", "a/b", "--pr", "3",
                "--image", "app", "--tag", "abc123",
                "--domain", "prev.internal", "--out", str(out),
            ]
        )
        text = (out / "kustomization.yaml").read_text()
        assert "newTag: abc123" in text
        assert "value: pr-3.prev.internal" in text


class TestCommentCommand:
    def test_fails_without_token(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        rc = main(["comment", "--repo", "a/b", "--pr", "1", "--sha", "abc"])
        assert rc == 2
        assert "GITHUB_TOKEN" in capsys.readouterr().err

    def test_upserts_via_client(self, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        calls = []

        class StubClient:
            def __init__(self, token, repo):
                calls.append(("init", token, repo))

            def upsert_comment(self, pr, body):
                calls.append(("upsert", pr, body))
                return "created"

        monkeypatch.setattr("previewctl.cli.comment_mod.GitHubClient", StubClient)
        rc = main(["comment", "--repo", "a/b", "--pr", "7", "--sha", "deadbeef"])
        assert rc == 0
        assert calls[0] == ("init", "t", "a/b")
        _, pr, body = calls[1]
        assert pr == 7
        assert "preview-b-pr-7" in body


class TestTeardownCommand:
    def test_dry_run_deletes_nothing(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "previewctl.cli.kube.delete_namespace",
            lambda ns: pytest.fail("should not delete on dry-run"),
        )
        rc = main(["teardown", "--repo", "a/b", "--pr", "3", "--dry-run"])
        assert rc == 0
        assert "would delete namespace preview-b-pr-3" in capsys.readouterr().out

    def test_deletes_namespace(self, monkeypatch):
        deleted = []
        monkeypatch.setattr(
            "previewctl.cli.kube.delete_namespace", deleted.append
        )
        assert main(["teardown", "--repo", "a/b", "--pr", "3"]) == 0
        assert deleted == ["preview-b-pr-3"]


class TestCleanupCommand:
    def test_dry_run_reports_expired(self, monkeypatch, capsys):
        from datetime import datetime, timedelta, timezone

        from previewctl.cleanup import PreviewEnvironment

        now = datetime.now(timezone.utc)
        envs = [
            PreviewEnvironment("preview-a-pr-1", now - timedelta(days=2), pr=1),
            PreviewEnvironment("preview-a-pr-2", now - timedelta(minutes=5), pr=2),
        ]
        monkeypatch.setattr("previewctl.cli.kube.list_preview_environments", lambda: envs)
        monkeypatch.setattr(
            "previewctl.cli.kube.delete_namespace",
            lambda ns: pytest.fail("should not delete on dry-run"),
        )
        rc = main(["cleanup", "--ttl", "24h", "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "would delete preview-a-pr-1" in out
        assert "preview-a-pr-2" not in out

    def test_deletes_only_expired(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        from previewctl.cleanup import PreviewEnvironment

        now = datetime.now(timezone.utc)
        envs = [
            PreviewEnvironment("preview-a-pr-1", now - timedelta(days=2), pr=1),
            PreviewEnvironment("preview-a-pr-2", now - timedelta(minutes=5), pr=2),
        ]
        deleted = []
        monkeypatch.setattr("previewctl.cli.kube.list_preview_environments", lambda: envs)
        monkeypatch.setattr("previewctl.cli.kube.delete_namespace", deleted.append)
        assert main(["cleanup", "--ttl", "24h"]) == 0
        assert deleted == ["preview-a-pr-1"]

    def test_nothing_to_clean(self, monkeypatch, capsys):
        monkeypatch.setattr("previewctl.cli.kube.list_preview_environments", lambda: [])
        assert main(["cleanup", "--ttl", "1h"]) == 0
        assert "nothing to clean up" in capsys.readouterr().out

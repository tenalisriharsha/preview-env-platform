import pytest

from previewctl.render import preview_host, render_kustomization, write_overlay


def render(**overrides):
    kwargs = {
        "namespace": "preview-app-pr-12",
        "base": "../../base",
        "image": "app",
        "tag": "pr-12",
        "pr": 12,
        "host": "pr-12.preview.example.com",
    }
    kwargs.update(overrides)
    return render_kustomization(**kwargs)


class TestRenderKustomization:
    def test_contains_all_wiring(self):
        out = render()
        assert "namespace: preview-app-pr-12" in out
        assert "- ../../base" in out
        assert "name: app" in out
        assert "newTag: pr-12" in out
        assert "value: pr-12.preview.example.com" in out

    def test_carries_preview_labels(self):
        out = render(pr=7)
        assert 'preview.env/platform: "true"' in out
        assert 'preview.env/pr: "7"' in out

    def test_targets_ingress_host_patch(self):
        out = render()
        assert "kind: Ingress" in out
        assert "path: /spec/rules/0/host" in out

    @pytest.mark.parametrize("field", ["namespace", "image", "tag", "host"])
    def test_rejects_empty_required_fields(self, field):
        with pytest.raises(ValueError, match=field):
            render(**{field: ""})

    def test_rejects_invalid_pr(self):
        with pytest.raises(ValueError):
            render(pr=0)


class TestPreviewHost:
    def test_builds_host(self):
        assert preview_host(12, "preview.example.com") == "pr-12.preview.example.com"

    def test_strips_domain_whitespace_and_dots(self):
        assert preview_host(1, " preview.example.com. ") == "pr-1.preview.example.com"

    def test_rejects_empty_domain(self):
        with pytest.raises(ValueError):
            preview_host(1, "  ")

    def test_rejects_invalid_pr(self):
        with pytest.raises(ValueError):
            preview_host(0, "preview.example.com")


class TestWriteOverlay:
    def test_writes_kustomization_file(self, tmp_path):
        write_overlay(tmp_path / "pr-12", render())
        written = (tmp_path / "pr-12" / "kustomization.yaml").read_text()
        assert "namespace: preview-app-pr-12" in written

    def test_creates_missing_parents(self, tmp_path):
        write_overlay(tmp_path / "deep" / "nested" / "pr-1", render())
        assert (tmp_path / "deep" / "nested" / "pr-1" / "kustomization.yaml").exists()

import pytest

from previewctl.naming import namespace_for_pr, repo_name, slugify


class TestSlugify:
    def test_lowercases_and_replaces_invalid_chars(self):
        assert slugify("My_App v2.0") == "my-app-v2-0"

    def test_collapses_dash_runs(self):
        assert slugify("a--b---c") == "a-b-c"

    def test_strips_leading_and_trailing_dashes(self):
        assert slugify("--app--") == "app"

    def test_empty_result_for_symbols_only(self):
        assert slugify("!!!") == ""


class TestRepoName:
    def test_extracts_name_part(self):
        assert repo_name("octocat/hello-world") == "hello-world"

    def test_tolerates_surrounding_slashes(self):
        assert repo_name("/octocat/hello-world/") == "hello-world"

    @pytest.mark.parametrize("bad", ["", "   ", "/"])
    def test_rejects_empty(self, bad):
        with pytest.raises(ValueError):
            repo_name(bad)


class TestNamespaceForPR:
    def test_basic_shape(self):
        assert namespace_for_pr("octocat/hello-world", 12) == "preview-hello-world-pr-12"

    def test_sanitizes_repo_name(self):
        assert namespace_for_pr("Octo_Cat/My.App", 3) == "preview-my-app-pr-3"

    def test_is_valid_dns1123_label(self):
        import re

        ns = namespace_for_pr("Some_Weird/Repo.Name With Spaces", 999)
        assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", ns)
        assert len(ns) <= 63

    def test_long_repo_names_are_truncated_to_63_chars(self):
        ns = namespace_for_pr("octocat/" + "a" * 100, 42)
        assert len(ns) == 63
        assert ns.endswith("-pr-42")
        assert not ns.endswith("--pr-42")

    def test_truncation_never_produces_trailing_dash_before_suffix(self):
        ns = namespace_for_pr("octocat/" + "x" * 48 + "-tail", 7)
        assert len(ns) <= 63
        assert "-pr-7" in ns

    def test_rejects_zero_and_negative_pr(self):
        with pytest.raises(ValueError):
            namespace_for_pr("a/b", 0)
        with pytest.raises(ValueError):
            namespace_for_pr("a/b", -5)

    def test_rejects_unusable_repo_name(self):
        with pytest.raises(ValueError):
            namespace_for_pr("owner/!!!", 1)

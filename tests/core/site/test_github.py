import base64
import unittest
from unittest.mock import MagicMock, patch

from core.site import github
from tests.support.config import patched_config, site_config


def response(status, body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = text
    return r


def patched_requests(*responses):
    return patch("core.site.client.requests.request", side_effect=list(responses))


def calls(mock):
    return [(c.args[0], c.args[1].split("/repos/o/r/")[1]) for c in mock.call_args_list]


class TestBranches(unittest.TestCase):
    def test_create_branch_reads_the_base_then_creates(self):
        with site_config(), patched_requests(response(200, {"object": {"sha": "abc"}}), response(201)) as req:
            self.assertTrue(github.create_branch("news/x", "main"))
        self.assertEqual(calls(req), [("GET", "git/ref/heads/main"), ("POST", "git/refs")])
        self.assertEqual(req.call_args_list[1].kwargs["json"], {"ref": "refs/heads/news/x", "sha": "abc"})

    def test_existing_branch_is_reused(self):
        with site_config(), patched_requests(response(200, {"object": {"sha": "abc"}}),
                                             response(422, text="Reference already exists")):
            self.assertTrue(github.create_branch("news/x", "main"))

    def test_missing_base_fails_without_creating(self):
        with site_config(), patched_requests(response(404, text="nope")) as req:
            self.assertFalse(github.create_branch("news/x", "main"))
        self.assertEqual(req.call_count, 1)

    def test_other_errors_fail(self):
        with site_config(), patched_requests(response(200, {"object": {"sha": "abc"}}), response(403, text="no")):
            self.assertFalse(github.create_branch("news/x", "main"))

    def test_branch_exists(self):
        with site_config(), patched_requests(response(200, {}), response(404)):
            self.assertTrue(github.branch_exists("news/x"))
            self.assertFalse(github.branch_exists("news/y"))

    def test_delete_branch(self):
        with site_config(), patched_requests(response(204), response(422, text="x")):
            self.assertTrue(github.delete_branch("news/x"))
            self.assertFalse(github.delete_branch("news/x"))


class TestFiles(unittest.TestCase):
    def test_put_new_file_commits_base64_without_sha(self):
        with site_config(), patched_requests(response(404), response(201)) as req:
            self.assertTrue(github.put_file("src/a b.md", "ciao è", "msg", "news/x"))
        self.assertEqual(calls(req), [("GET", "contents/src/a%20b.md"), ("PUT", "contents/src/a%20b.md")])
        payload = req.call_args_list[1].kwargs["json"]
        self.assertEqual(base64.b64decode(payload["content"]).decode(), "ciao è")
        self.assertEqual((payload["branch"], payload["message"]), ("news/x", "msg"))
        self.assertNotIn("sha", payload)

    def test_put_existing_file_sends_its_sha(self):
        with site_config(), patched_requests(response(200, {"sha": "old"}), response(200)) as req:
            self.assertTrue(github.put_file("a.md", "x", "m", "b"))
        self.assertEqual(req.call_args_list[1].kwargs["json"]["sha"], "old")

    def test_put_failure(self):
        with site_config(), patched_requests(response(404), response(422, text="bad")):
            self.assertFalse(github.put_file("a.md", "x", "m", "b"))

    def test_get_file_text_decodes(self):
        body = {"content": base64.b64encode("àè".encode()).decode()}
        with site_config(), patched_requests(response(200, body), response(404)):
            self.assertEqual(github.get_file_text("a.md", "main"), "àè")
            self.assertIsNone(github.get_file_text("b.md", "main"))


class TestListDir(unittest.TestCase):
    def test_lists_only_files(self):
        body = [{"name": "a.md", "type": "file"}, {"name": "sub", "type": "dir"}, {"name": "b.md", "type": "file"}]
        with site_config(), patched_requests(response(200, body)) as req:
            self.assertEqual(github.list_dir("src/content/news", "main"), ["a.md", "b.md"])
        self.assertEqual(req.call_args.kwargs["params"], {"ref": "main"})

    def test_missing_folder_is_empty(self):
        with site_config(), patched_requests(response(404)):
            self.assertEqual(github.list_dir("src/content/collaborazioni", "main"), [])

    def test_other_errors_raise(self):
        with site_config(), patched_requests(response(500)):
            with self.assertRaises(github.GitHubError):
                github.list_dir("src/content/news", "main")


class TestMerge(unittest.TestCase):
    def test_merge_success_codes(self):
        with site_config(), patched_requests(response(201), response(204)) as req:
            self.assertTrue(github.merge_branch("news/x", "main", "Pubblica"))
            self.assertTrue(github.merge_branch("news/x", "main", "Pubblica"))
        self.assertEqual(req.call_args.kwargs["json"], {"base": "main", "head": "news/x", "commit_message": "Pubblica"})

    def test_merge_conflict_makes_exactly_one_call(self):
        with site_config(), patched_requests(response(409, text="conflict")) as req:
            self.assertFalse(github.merge_branch("news/x", "main", "m"))
        self.assertEqual(req.call_count, 1)


class TestNotConfigured(unittest.TestCase):
    def test_no_token_makes_no_network_call(self):
        with patched_config(ASTRO_GITHUB_TOKEN=None), patch("core.site.client.requests.request") as req:
            with self.assertRaises(RuntimeError):
                github.create_branch("news/x", "main")
        req.assert_not_called()


if __name__ == "__main__":
    unittest.main()

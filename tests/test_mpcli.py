"""End-to-end tests driving germidiff-mp against a stand-in Launchpad."""

# Copyright (C) 2026 Canonical Ltd.
#
# germidiff is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as
# published by the Free Software Foundation.
#
# germidiff is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with germidiff; see the file COPYING.  If not, see
# <https://www.gnu.org/licenses/>.

import io
import os
from contextlib import redirect_stdout

from germidiff import cli, launchpad, mpcli
from germidiff.mpcli import main, parse_args
from tests.helpers import FAKE_GERMINATE, GitTestCase

API = launchpad.API_ROOT


class MpTestCase(GitTestCase):
    """A whole little Launchpad: two git repos and a canned API answer."""

    def setUp(self):
        super().setUp()
        self.temp_dir = self.make_temp_dir()
        self.upstream = os.path.join(self.temp_dir, "upstream")
        self.cache = os.path.join(self.temp_dir, "cache")
        self.chdist_base = os.path.join(self.temp_dir, "chdists")
        self.write(
            os.path.join(
                self.chdist_base, "questing", "etc", "apt", "apt.conf"
            ),
            'Apt {\n   Architecture "ppc64el";\n};\n'
            'Dir "%s";\n' % self.temp_dir,
        )
        self.write(
            os.path.join(
                self.chdist_base, "questing", "etc", "apt", "sources.list"
            ),
            "deb http://archive.example/ubuntu questing main restricted\n",
        )
        # Answer for the one API request the run makes.
        self.fetched = []
        self.proposal = {}
        self.original_get = launchpad._get_json
        launchpad._get_json = self._fetch
        self.addCleanup(self._restore)

    def _restore(self):
        launchpad._get_json = self.original_get

    def _fetch(self, url):
        self.fetched.append(url)
        return self.proposal

    def make_collection(self, name, structure, seeds, branch="questing"):
        repo = self.make_repo(os.path.join(self.upstream, name))
        self.write_collection(repo, structure, seeds)
        self.git(repo, "checkout", "-q", "-b", branch)
        self.commit(repo, "initial")
        return repo

    def fork(self, name, source="ubuntu", branch="proposed"):
        """Clone a collection the way a contributor would, on a new branch."""
        path = os.path.join(self.temp_dir, "forks", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.git(self.temp_dir, "clone", "-q",
                 os.path.join(self.upstream, source), path)
        self.git(path, "checkout", "-q", "-b", branch)
        return path

    def set_proposal(
        self,
        source_repo,
        source_branch="proposed",
        target_repo="ubuntu",
        target_branch="questing",
        status="Needs review",
    ):
        self.proposal = {
            "resource_type_link": API + "#branch_merge_proposal",
            "web_link":
                "https://code.launchpad.net/~someone/ubuntu-seeds/+git/"
                "%s/+merge/42" % target_repo,
            "source_git_repository_link":
                API + "~someone/ubuntu-seeds/+git/" + source_repo,
            "source_git_path": "refs/heads/" + source_branch,
            "target_git_repository_link":
                API + "~ubuntu-core-dev/ubuntu-seeds/+git/" + target_repo,
            "target_git_path": "refs/heads/" + target_branch,
            "queue_status": status,
            "commit_message": "change some seeds",
        }

    def run_mp(self, *args, **kwargs):
        """Run germidiff-mp, with the forks reachable as git URLs.

        ``urls`` maps the repository name Launchpad reports to the local
        directory standing in for it.
        """
        urls = kwargs.pop("urls", {})
        original = launchpad.git_url_for

        def git_url_for(api_link):
            name = launchpad.repo_name(api_link)
            if name in urls:
                return urls[name]
            return os.path.join(self.upstream, name)

        launchpad.git_url_for = git_url_for
        mpcli.git_url_for = git_url_for
        self.addCleanup(self._restore_git_url, original)

        argv = [
            "https://code.launchpad.net/~someone/ubuntu-seeds/+git/ubuntu"
            "/+merge/42",
            "--germinate", FAKE_GERMINATE,
            "--cache-dir", self.cache,
            "--chdist-base", self.chdist_base,
            "--chdist", "questing",
            "--seed-source", self.upstream + "/",
            "--no-update",
            "--quiet",
        ] + list(args)
        out = io.StringIO()
        with redirect_stdout(out):
            status = main(argv)
        return status, out.getvalue()

    def _restore_git_url(self, original):
        launchpad.git_url_for = original
        mpcli.git_url_for = original


class TestEndToEnd(MpTestCase):
    def setUp(self):
        super().setUp()
        self.make_collection("platform", "base:\n", {"base": ["libc"]})
        self.make_collection(
            "ubuntu",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )

    def test_diffs_a_proposal_from_a_fork(self):
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")
        self.set_proposal("ubuntu")

        status, text = self.run_mp(urls={"ubuntu": fork})

        self.assertEqual(0, status)
        self.assertIn("+thunderbird", text)

    def test_reports_which_proposal_it_diffed(self):
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")
        self.set_proposal("ubuntu")

        _, text = self.run_mp(urls={"ubuntu": fork})

        self.assertIn("+merge/42", text.splitlines()[0])
        self.assertIn("ubuntu.questing", text.splitlines()[1])

    def test_no_header_leaves_just_the_report(self):
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")
        self.set_proposal("ubuntu")

        _, text = self.run_mp("--no-header", urls={"ubuntu": fork})

        self.assertNotIn("+merge/42", text)
        self.assertIn("+thunderbird", text)

    def test_diffs_from_the_merge_base_not_the_target_tip(self):
        # Something else lands on the target while the proposal waits.  It is
        # not part of what the proposal does, and must not be reported as if
        # it were.
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")

        upstream = os.path.join(self.upstream, "ubuntu")
        self.write_collection(
            upstream,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "someone-elses-package"]},
        )
        self.commit(upstream, "land something else")

        self.set_proposal("ubuntu")
        _, text = self.run_mp(urls={"ubuntu": fork})

        self.assertIn("+thunderbird", text)
        self.assertNotIn("someone-elses-package", text)

    def test_the_included_collection_is_fetched_too(self):
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")
        self.set_proposal("ubuntu")

        self.run_mp(urls={"ubuntu": fork})

        self.assertTrue(
            os.path.isfile(
                os.path.join(self.cache, "platform.questing", "STRUCTURE")
            )
        )

    def test_a_collection_the_change_adds_is_fetched(self):
        # The include line exists on only one side, and germinating that side
        # needs it all the same.
        self.make_collection("extras", "more:\n", {"more": ["gimp"]})
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ninclude extras.questing\n"
            "desktop: base more\n",
            {"desktop": ["firefox"]},
        )
        self.commit(fork, "include extras")
        self.set_proposal("ubuntu")

        status, _ = self.run_mp(urls={"ubuntu": fork})

        self.assertEqual(0, status)
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.cache, "extras.questing", "STRUCTURE")
            )
        )

    def test_a_proposal_within_one_repo_works(self):
        # Nothing says the source has to be a fork.
        upstream = os.path.join(self.upstream, "ubuntu")
        self.git(upstream, "checkout", "-q", "-b", "proposed")
        self.write_collection(
            upstream,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(upstream, "seed thunderbird")
        self.git(upstream, "checkout", "-q", "questing")
        self.set_proposal("ubuntu")

        status, text = self.run_mp()

        self.assertEqual(0, status)
        self.assertIn("+thunderbird", text)

    def test_dry_run_says_what_it_would_do_without_germinating(self):
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")
        self.set_proposal("ubuntu")

        status, text = self.run_mp("--dry-run", urls={"ubuntu": fork})

        self.assertEqual(0, status)
        self.assertIn("not germinating", text)
        self.assertNotIn("thunderbird", text)

    def test_the_cache_is_reused_between_runs(self):
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")
        self.set_proposal("ubuntu")

        self.run_mp(urls={"ubuntu": fork})
        # A marker inside the clone survives a fetch but not a fresh clone.
        marker = self.write(
            os.path.join(self.cache, "platform.questing", ".git", "marker"),
            "still here\n",
        )

        status, text = self.run_mp(urls={"ubuntu": fork})

        self.assertEqual(0, status)
        self.assertIn("+thunderbird", text)
        self.assertTrue(os.path.isfile(marker))


class TestFailures(MpTestCase):
    def test_a_url_that_is_not_a_proposal_is_an_error(self):
        out = io.StringIO()
        with redirect_stdout(out):
            status = main(["https://example.org/nope"])
        self.assertEqual(1, status)

    def test_an_unreachable_source_repo_is_an_error(self):
        self.make_collection("platform", "base:\n", {"base": ["libc"]})
        self.make_collection(
            "ubuntu",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        self.set_proposal("ubuntu")

        status, _ = self.run_mp(
            urls={"ubuntu": os.path.join(self.temp_dir, "absent")}
        )

        self.assertEqual(1, status)


class TestArguments(MpTestCase):
    def parse(self, *args):
        return parse_args(
            ["https://code.launchpad.net/~a/b/+git/c/+merge/1"] + list(args)
        )

    def test_components_default_to_what_the_collection_implies(self):
        args = self.parse()
        self.assertEqual(
            ("main", "restricted"), cli.components_for(args, "ubuntu")
        )
        self.assertEqual(
            ("main", "restricted", "universe", "multiverse"),
            cli.components_for(args, "kubuntu"),
        )

    def test_components_can_be_overridden(self):
        args = self.parse("--components", "main,universe")
        self.assertEqual(
            ("main", "universe"), cli.components_for(args, "ubuntu")
        )
        args = self.parse("--components", "main universe")
        self.assertEqual(
            ("main", "universe"), cli.components_for(args, "ubuntu")
        )

    def test_work_dir_implies_keep(self):
        self.assertTrue(self.parse("--work-dir", "/tmp/x").keep)

    def test_analysis_options_are_offered_here_too(self):
        args = self.parse("--whole-seed-lists", "--probe-retention")
        self.assertTrue(args.whole_seed_lists)
        self.assertTrue(args.probe_retention)


class TestChdistHandling(MpTestCase):
    """Making, taking and refreshing the archive metadata."""

    def setUp(self):
        super().setUp()
        self.use_stub_chdist(self.temp_dir)
        self.fresh_base = os.path.join(self.temp_dir, "new-chdists")
        self.make_collection("platform", "base:\n", {"base": ["libc"]})
        self.make_collection(
            "ubuntu",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )

    def propose(self):
        fork = self.fork("mine")
        self.write_collection(
            fork,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "thunderbird"]},
        )
        self.commit(fork, "seed thunderbird")
        self.set_proposal("ubuntu")
        return {"ubuntu": fork}

    def run_derived(self, *args, **kwargs):
        """Run without the --chdist and --no-update the other tests pass."""
        base = kwargs.pop("base", self.fresh_base)
        urls = self.propose()
        argv = [
            "https://code.launchpad.net/~someone/ubuntu-seeds/+git/ubuntu"
            "/+merge/42",
            "--germinate", FAKE_GERMINATE,
            "--cache-dir", self.cache,
            "--chdist-base", base,
            "--seed-source", self.upstream + "/",
            "--quiet",
        ] + list(args)
        original = launchpad.git_url_for

        def git_url_for(api_link):
            name = launchpad.repo_name(api_link)
            return urls.get(name, os.path.join(self.upstream, name))

        launchpad.git_url_for = git_url_for
        mpcli.git_url_for = git_url_for
        self.addCleanup(self._restore_git_url, original)
        out = io.StringIO()
        with redirect_stdout(out):
            status = main(argv)
        return status, out.getvalue()

    def test_the_proposal_says_which_chdist_to_make(self):
        status, text = self.run_derived()

        self.assertEqual(0, status)
        self.assertIn("+thunderbird", text)
        created = self.chdist_calls()[0]
        self.assertIn("create questing", created)
        self.assertIn("main restricted", created)

    def test_the_chdist_is_refreshed_once(self):
        # germidiff-mp settles the chdist before germinating and hands it
        # down; the diff must not pay for a second apt-get update.
        self.run_derived()

        updates = [
            call for call in self.chdist_calls() if "apt-get" in call
        ]
        self.assertEqual(1, len(updates))

    def test_a_chdist_given_is_refreshed_too(self):
        status, text = self.run_derived(
            "--chdist", "questing", base=self.chdist_base
        )

        self.assertEqual(0, status)
        self.assertEqual(
            ["-d %s apt-get questing update" % self.chdist_base],
            self.chdist_calls(),
        )

    def test_no_update_leaves_the_lists_as_they_stand(self):
        status, text = self.run_derived(
            "--chdist", "questing", "--no-update", base=self.chdist_base
        )

        self.assertEqual(0, status)
        self.assertEqual([], self.chdist_calls())

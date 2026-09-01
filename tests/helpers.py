"""Testing helpers."""

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

import os
import shutil
import subprocess
import tempfile
import unittest

FAKE_GERMINATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fake_germinate.py"
)


class TestCase(unittest.TestCase):
    def make_temp_dir(self):
        temp_dir = tempfile.mkdtemp(prefix="germidiff-test-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        return temp_dir

    def write(self, path, text):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="UTF-8") as f:
            f.write(text)
        return path

    def write_collection(self, directory, structure, seeds):
        """Write a seed collection: a STRUCTURE file and its seed files."""
        self.write(os.path.join(directory, "STRUCTURE"), structure)
        for name, packages in seeds.items():
            self.write(
                os.path.join(directory, name),
                "".join(" * %s\n" % pkg for pkg in packages),
            )
        return directory


class GitTestCase(TestCase):
    def git(self, repo, *args):
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.org",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.org",
        )
        proc = subprocess.run(
            ["git", "-C", repo] + list(args),
            capture_output=True,
            encoding="UTF-8",
            env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "git %s failed: %s" % (" ".join(args), proc.stderr)
            )
        return proc.stdout.strip()

    def make_repo(self, directory):
        os.makedirs(directory, exist_ok=True)
        self.git(directory, "init", "-q", "-b", "main")
        return directory

    def commit(self, repo, message):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

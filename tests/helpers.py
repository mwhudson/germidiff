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


# A stub standing in for devscripts' chdist, recording how it was called and
# writing just enough of a chdist for the checks to have something to read.
STUB_CHDIST = """\
#! /usr/bin/env python3
import os
import sys

args = sys.argv[1:]
with open(os.environ["CHDIST_LOG"], "a") as log:
    log.write(" ".join(args) + "\\n")

base = args[args.index("-d") + 1]
if not os.path.isdir(base):
    # The real chdist resolves its data directory with abs_path(), which
    # gives up on one that does not exist and leaves it working from an
    # undefined path.
    sys.stderr.write("can't open dir %s\\n" % base)
    sys.exit(1)
if "create" in args:
    rest = args[args.index("create") + 1:]
    name, mirror, series, components = rest[0], rest[1], rest[2], rest[3:]
    arch = args[args.index("-a") + 1] if "-a" in args else "amd64"
    directory = os.path.join(base, name, "etc", "apt")
    os.makedirs(directory)
    with open(os.path.join(directory, "sources.list"), "w") as f:
        f.write("deb %s %s %s\\n" % (mirror, series, " ".join(components)))
    with open(os.path.join(directory, "apt.conf"), "w") as f:
        f.write('Apt {\\n   Architecture "%s";\\n};\\n' % arch)
"""


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

    def use_stub_chdist(self, directory):
        """Put a stub chdist on $PATH, logging how it is called."""
        self.chdist_log = os.path.join(directory, "chdist-calls")
        stub_dir = os.path.join(directory, "bin")
        path = self.write(os.path.join(stub_dir, "chdist"), STUB_CHDIST)
        os.chmod(path, 0o755)
        original = os.environ["PATH"]
        os.environ["PATH"] = stub_dir + os.pathsep + original
        os.environ["CHDIST_LOG"] = self.chdist_log
        self.addCleanup(os.environ.pop, "CHDIST_LOG", None)
        self.addCleanup(os.environ.__setitem__, "PATH", original)

    def chdist_calls(self):
        """What the stub chdist was run with, in order."""
        if not os.path.exists(self.chdist_log):
            return []
        with open(self.chdist_log) as f:
            return [line.strip() for line in f if line.strip()]


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

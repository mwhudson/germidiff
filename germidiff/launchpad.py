"""Reading a merge proposal from Launchpad.

Everything here is read-only and anonymous: a merge proposal on a public
seed repo is public, so resolving one needs no credentials and nothing that
could write to Launchpad.  Posting the report back is a separate concern with
a very different risk profile, and deliberately lives nowhere in this module.

Reads go straight to the API over HTTP rather than through launchpadlib.
The one thing we need is three fields of one object, launchpadlib's WADL
round-trip costs more than the request it is setting up, and it drags in an
OAuth stack that read-only anonymous access has no use for.
"""

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

import json
import logging
import re
import urllib.error
import urllib.request

__all__ = [
    "LaunchpadError",
    "MergeProposal",
    "api_url_for",
    "git_url_for",
    "load_merge_proposal",
    "repo_name",
]

_logger = logging.getLogger("germidiff")

API_ROOT = "https://api.launchpad.net/devel/"
WEB_ROOT = "https://code.launchpad.net/"
GIT_ROOT = "https://git.launchpad.net/"

# The one thing about a merge proposal URL we insist on recognising: it has
# to name a merge proposal, not the branch or repo it lives under.
_MERGE_PATH = re.compile(r"/\+merge/\d+/?$")

USER_AGENT = "germidiff (+https://git.launchpad.net/germidiff)"

TIMEOUT = 30


class LaunchpadError(Exception):
    """A merge proposal could not be read from Launchpad."""


def api_url_for(reference):
    """Turn a merge proposal reference into its Launchpad API URL.

    Accepts the API URL itself or the ``code.launchpad.net`` web URL people
    actually paste around; the two share a path, so this is a prefix swap.
    A bare proposal number is not accepted: Launchpad has no global lookup
    by number, and the path a proposal lives under is not derivable from it.
    """
    reference = reference.strip()
    if not reference:
        raise LaunchpadError("no merge proposal given")

    if reference.isdigit():
        raise LaunchpadError(
            "%s is a merge proposal number, and Launchpad has no way to look "
            "one up on its own; give the proposal's URL instead" % reference
        )

    for root in (API_ROOT, WEB_ROOT):
        if reference.startswith(root):
            path = reference[len(root):]
            break
    else:
        raise LaunchpadError(
            "%s is not a Launchpad merge proposal URL; expected one starting "
            "%s" % (reference, WEB_ROOT)
        )

    if not _MERGE_PATH.search("/" + path):
        raise LaunchpadError(
            "%s does not name a merge proposal (no /+merge/<number>)"
            % reference
        )
    return API_ROOT + path.rstrip("/")


def repo_name(api_link):
    """The name of a git repository, from its API or git URL.

    ``.../~ubuntu-core-dev/ubuntu-seeds/+git/ubuntu`` is the ``ubuntu`` seed
    collection.  The owner and project vary -- a contributor proposes from
    their own fork, which may not even share the name -- so only the last
    path segment says which collection is meant.
    """
    return api_link.rstrip("/").rsplit("/", 1)[-1]


def git_url_for(api_link):
    """The anonymous git URL for a repository, from its API URL."""
    if not api_link.startswith(API_ROOT):
        raise LaunchpadError("%s is not a Launchpad API URL" % api_link)
    return GIT_ROOT + api_link[len(API_ROOT):].rstrip("/")


def _short_ref(git_path):
    """``refs/heads/foo`` as ``foo``, and anything else unchanged."""
    if git_path and git_path.startswith("refs/heads/"):
        return git_path[len("refs/heads/"):]
    return git_path


class MergeProposal:
    """What we need to know about a merge proposal to germinate it."""

    def __init__(
        self,
        url,
        source_repo,
        source_ref,
        target_repo,
        target_ref,
        status=None,
        commit_message=None,
    ):
        self.url = url
        self.source_repo = source_repo
        self.source_ref = source_ref
        self.target_repo = target_repo
        self.target_ref = target_ref
        self.status = status
        self.commit_message = commit_message

    @property
    def collection(self):
        """The seed collection under test.

        Taken from the *target*: a proposal is a request to change that
        collection, whatever the fork it arrives from happens to be called.
        """
        return repo_name(self.target_repo)

    @property
    def series(self):
        """The Ubuntu series, which is the target branch's name."""
        return _short_ref(self.target_ref)

    @property
    def branch(self):
        """The collection's branch name, as an ``include`` line spells it."""
        return "%s.%s" % (self.collection, self.series)

    def __repr__(self):  # pragma: no cover
        return "<MergeProposal %s>" % self.url


def _get_json(url):
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LaunchpadError("%s: no such merge proposal" % url)
        if e.code in (401, 403):
            raise LaunchpadError(
                "%s: not readable anonymously; germidiff does not log in, so "
                "a private proposal is out of reach" % url
            )
        raise LaunchpadError("%s: %s" % (url, e))
    except urllib.error.URLError as e:
        raise LaunchpadError("could not reach %s: %s" % (url, e.reason))
    except OSError as e:
        raise LaunchpadError("could not reach %s: %s" % (url, e))

    try:
        return json.loads(body.decode("UTF-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise LaunchpadError("%s returned something that is not JSON: %s"
                             % (url, e))


def load_merge_proposal(reference, fetch=None):
    """Read a merge proposal from Launchpad.

    ``fetch`` is the function used to GET a URL and decode the JSON; it exists
    so tests can answer without a network.
    """
    if fetch is None:
        fetch = _get_json

    url = api_url_for(reference)
    _logger.info("reading %s", url)
    data = fetch(url)

    if data.get("resource_type_link", "").endswith("#branch_merge_proposal"):
        pass
    elif "target_git_repository_link" not in data:
        raise LaunchpadError("%s is not a merge proposal" % reference)

    target_repo = data.get("target_git_repository_link")
    source_repo = data.get("source_git_repository_link")
    if not target_repo or not source_repo:
        # Bazaar proposals have source_branch_link instead, and predate every
        # seed branch germinate would be asked about today.
        raise LaunchpadError(
            "%s is not a git merge proposal; germidiff cannot diff a Bazaar "
            "branch" % data.get("web_link", reference)
        )

    return MergeProposal(
        url=data.get("web_link") or url,
        source_repo=source_repo,
        source_ref=data.get("source_git_path"),
        target_repo=target_repo,
        target_ref=data.get("target_git_path"),
        status=data.get("queue_status"),
        commit_message=data.get("commit_message"),
    )

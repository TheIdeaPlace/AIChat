"""Native probe harness for a new agent: COPY this outside the repository.

Every Muse Code feature was checked this way before (and after) it was built,
and most of the surprises in references/lessons.md came from such a run.

How to run (from a throwaway folder, never the repo):

    mkdir -p "$TEMP/tcp-<agent>-probe/scripts"
    cp probe_template.py "$TEMP/tcp-<agent>-probe/scripts/probe.py"
    W=$(mktemp -d /c/dev/tcp-<agent>-probe-XXXX)
    cd "$W" && <venv>/python -I "$TEMP/tcp-<agent>-probe/scripts/probe.py" \
        <path-to-the-worktree> "$(cygpath -w "$W")"
    cd /c; rm -rf "$W"

Rules this harness keeps (see SKILL.md, "Privacy while probing"):
- synthetic prompts only, in a throwaway workspace that is deleted after;
- log method names and response *shapes*, cut short, never your real chats;
- when reading the user's real conversations, count kinds, print no content;
- probe a method that isn't allowlisted yet through the connection's internal
  request call, for verification only; the app's allowlist stays unchanged
  until the feature is built.

Fill in the TODO parts for the new agent: how to start its host, and the
calls to try. The Muse versions are left as comments to copy from.
"""
import json
import sys
import time
from collections import Counter

sys.path.insert(0, sys.argv[1])           # the worktree, so the app's own code is used
WORKSPACE = sys.argv[2]

# TODO: import the new provider's connection and owned-chats classes.
# Muse:
# from thechatplace import platform_paths
# from thechatplace.providers.muse_connection import ManagedMuseExecution, MuseRequestError
# from thechatplace.providers.muse import MuseOwnedChats, _command_id


def logged(execution):
    """Wrap a connection so each request and the shape of its reply is printed,
    cut short. Errors print their code, which is what the refusals turn on."""
    real = execution.request

    def request(method, params):
        print(">>", method, json.dumps(params)[:300])
        try:
            result = real(method, params)
        except Exception as error:  # noqa: BLE001 - a probe reports everything
            print("!!", method, type(error).__name__, getattr(error, "code", ""))
            raise
        print("<<", method, json.dumps(result)[:600])
        return result
    execution.request = request
    return execution


def raw(execution, method, params):
    """A method not yet allowlisted, for verification only (Muse: the managed
    connection's underlying connection's _request)."""
    # TODO: Muse:
    # execution.request("permissionProfile/list", {})   # make sure the host is up
    # return execution._get()._request(method, params)
    raise NotImplementedError


def wait(poll, limit=240, every=2.0):
    """Poll until the agent itself says the turn ended. Never treat quiet
    polls as completion (references/lessons.md, section 3)."""
    deadline = time.time() + limit
    while time.time() < deadline:
        state = poll()
        if state in ("idle", "unknown"):
            return state
        time.sleep(every)
    return "timeout"


def kinds_only(items):
    """What a real conversation contains, as counts of kinds: never its text."""
    return dict(Counter(item.get("kind", "?") for item in items if isinstance(item, dict)))


def main():
    # TODO: start the agent's host the way the app does.
    # Muse:
    # execution = logged(ManagedMuseExecution(platform_paths.find_muse().path))
    # chats = MuseOwnedChats(execution); chats.set_persistence(lambda record: None)
    execution = None
    try:
        # TODO: the steps to check, one question each. Muse's first run was:
        # rec = chats.create(WORKSPACE, "TCP native check")
        # chats.send(rec, "This is a protocol test. Reply with the single word: pong. Use no tools.")
        # print("ended", wait(lambda: chats.poll(rec)["state"]))
        # And for a feature not yet built, e.g. steering:
        # print(raw(execution, "turn/steer", {...}))
        raise NotImplementedError("Fill in the agent-specific steps.")
    finally:
        if execution is not None:
            execution.close()


if __name__ == "__main__":
    main()

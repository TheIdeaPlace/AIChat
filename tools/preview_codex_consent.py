"""Preview real connected-tool dialogs with synthetic data and no service access.

Run from a source checkout with its development dependencies installed:
    python tools/preview_codex_consent.py sign-in
    python tools/preview_codex_consent.py approval
    python tools/preview_codex_consent.py long-approval

These previews test focus, reading and buttons only. No browser is opened,
no request is sent, and no chat or configuration is created.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def preview_request(kind):
    from thechatplace.providers.codex_requests import decode_request, ELICITATION
    params = {"threadId": "preview-thread", "serverName": "practice-tool"}
    if kind == "sign-in":
        params.update(mode="url", message="Practice sign-in. No browser will open.",
                      url="https://example.com/practice-sign-in", elicitationId="preview-login")
    else:
        arguments = {"title": "Practice note", "body": "A disposable note."}
        if kind == "long-approval":
            arguments["body"] = "Practice content. " * 400
        params.update(mode="form", message="Allow Practice tool to create a note?",
                      requestedSchema={"type": "object", "properties": {}},
                      _meta={"codex_approval_kind": "mcp_tool_call", "tool_params": arguments})
    return decode_request("preview-request", ELICITATION, params).elicitation()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("sign-in", "approval", "long-approval"))
    args = parser.parse_args()
    import wx
    from thechatplace.ui.dialogs import ElicitationDialog
    from thechatplace.ui import mac_keys
    app = wx.App(False)
    mac_keys.install(app)
    dialog = ElicitationDialog(None, "Practice only", preview_request(args.kind), feedback=print)
    try:
        dialog.ShowModal()
        print("Practice answer:", dialog.action or "answer later")
        print("Nothing was sent or opened.")
    finally:
        dialog.Destroy()


if __name__ == "__main__":
    main()

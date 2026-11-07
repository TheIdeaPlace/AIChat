# Codex workflow validation (#467)

The implementation is stacked on Claude's #468 integration. The detailed
coverage and remaining work are in
[the implementation note](../dev-notes/codex-feature-gaps.md).

## Visual review

Six Codex surfaces and two Claude regression surfaces were photographed in
Light and Aquatic High Contrast, sixteen PNGs total. Every capture was opened by
the implementer and an independent reviewer. No layout flags or new visual
defects were found. Pixel contrast and descriptions are recorded in
[the visual guide](visual/descriptions.md#codex-conversation-workflows-467).

| Surface | Light | High Contrast |
|---|---|---|
| codex-attachments | PASS | PASS |
| codex-tools | PASS | PASS |
| codex-commands | PASS | PASS |
| codex-collaboration | PASS | PASS |
| codex-rewind | PASS | PASS |
| codex-aside | PASS | PASS |
| connected-tools (Claude) | PASS | PASS |
| ask-aside (Claude) | PASS | PASS |

PASS here means no new visual defect. The main attachment surface inherits
the existing native-list focus-indicator problem tracked in #181; passing text
contrast does not close that issue.

The native no-prompt probes used Codex 0.163.0 and verified new-thread memory
mode, native Goal inspection, empty terminal inventory, and Plan preset
selection through the persistence barrier. They sent no model prompt and did
not authenticate a real third-party MCP service.

The eight new GUI workflow tests passed sequentially in **ClaudeTesting-2**.
The separate focused backend/RPC review run passed 309 tests; another focused
backend run passed 520 with one skip. The initial complete run reported 4176
passed, 58 skipped and seven failures, followed by a native wxPython shutdown
exception. All seven failed cases then passed after two disabled-runtime UI
guard fixes, installing Git in the VM, and using a short pytest fixture path.
A clean complete-suite rerun remains pending.

Real keyboard walks passed for Connected Tools, conversation Rewind, and the
Plan/Default picker: Tab and Shift+Tab reached the expected controls, and Escape
returned to Messages. The command picker, Ask Aside and attachment-list keyboard
walks remain pending. Baseline comparison reported sixteen new captures with
no missing or failed captures; all sixteen reviewed captures were accepted.

JAWS/NVDA listening and actual third-party credentials need a person; exact
steps and expected announcements are in the implementation note. Native
model-generated output, MCP authentication/forms, cloud and remote execution
are not claimed by the metadata probes or synthetic tests.

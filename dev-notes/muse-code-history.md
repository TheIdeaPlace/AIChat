# Muse Code: session discovery and read-only history

Second milestone; no issue number assigned. The plan below is implemented in
this checkout, with validation limits recorded at the end. Builds on
[Muse onboarding](muse-code-onboarding.md).

## Outcome

Existing Muse conversations appear in the shared session list and can be read
through the existing conversation controls. Opening or refreshing a conversation
must not start a turn, request workspace trust, execute tools or change Muse's
history. New chats and execution remain a later milestone.

## Work order

1. **Verify the history interface first.** Inspect the official protocol and
   installed Muse 1.4.18 behavior for session listing, pagination, history reads,
   stable identity, timestamps, archived sessions and status. Establish whether
   those operations are available without authentication and whether reading
   requires a session load/resume with side effects. Use synthetic conversations
   in a disposable workspace; record evidence without private history. Do not
   assume Codex-shaped methods or parse undocumented storage as a fallback.
   If no side-effect-free read interface exists, record that blocker before
   implementing discovery.
2. **Add a dedicated history connection.** Preserve the account connection's
   strict account-only method allowlist. Reuse safe process/framing primitives
   where appropriate, but give history its own verified method allowlist and
   lifecycle. Bound frames, pagination, request times and shutdown; fail safely
   on incompatible schemas, malformed responses and process exit. No account
   polling, browser offers or login prompts from background discovery.
3. **Implement the provider and reader.** Add a Muse adapter under
   `thechatplace/providers/`, using `Snapshot`, `SessionInfo`, `Transcript` and
   the conversation-provider contracts. Map native IDs to provider-qualified
   identities; do not infer TCP ownership from an external session. Preserve
   message order, roles and readable tool/error content. Unknown records must
   not crash the reader or imply supported actions. Use status unavailable
   unless Muse establishes a fresh live state.
4. **Integrate existing discovery and presentation.** Register through
   `SessionController` and the existing discovery registry. Audit provider
   selectors, filters, labels and settings for hard-coded provider lists.
   Enable reading only: send, create, stop, fork, delete, approvals and other
   unsupported operations stay unavailable. Do not add Muse to
   `RUNTIME_PROVIDERS` as a shortcut. Reuse the session list and conversation
   controls, preserving selection and focus during background refresh.
   Name the provider Muse Code and provide a keyboard-readable explanation
   that replies must be made in Muse Code. Preserve the existing Enter-to-open
   and return-to-list workflow. Do not guess whether a session originated in
   a terminal or editor when the protocol cannot establish its source.
5. **Handle failure and recovery.** Keep healthy providers usable when Muse
   is missing, signed out, incompatible or unavailable. Retain cached rows as
   stale using the existing discovery behavior; distinguish an outage from an
   empty session list. Provide safe persistent status and a concise announcement
   when a failure first occurs. A successful refresh clears it. Retry must not
   repeatedly speak the same error, steal focus or expose raw protocol errors.
6. **Validate and document.** Write failing synthetic tests before each
   implementation slice. Cover pagination, malformed/unknown records, duplicate
   IDs across providers, empty/missing history, outages and recovery, late
   callbacks, shutdown and explicitly denied write operations. Run focused
   provider/controller/UI tests, then the full suite. Update README, generated
   guides, open release notes, smoke imports and a manual history testing guide.

## Acceptance criteria

- A synthetic Muse conversation can be listed, opened, read, refreshed and
  reopened after restarting The Chat Place, without creating or running a turn.
- All discovered Muse sessions remain externally owned and read-only, with no
  route into another provider's runner or storage.
- Multiple pages load without silent truncation or unbounded loops; a partial
  failure is distinguishable from complete history.
- Missing or unavailable Muse does not block startup, other providers or app
  shutdown. Late results cannot replace another conversation or reach a closed
  window. Unknown live state cannot trigger a false finished-turn announcement.
- Keyboard navigation, existing conversation reading, copy and focus restoration
  work with NVDA 2026.3.1 (2026.3.1.58288) and JAWS 2027.2610.428
  (Script Revision: 0). User-reported passes are recorded separately from tests.
- Any new status/error notice uses the application's native OK-dialog pattern;
  persistent read-only history remains in the normal conversation controls.
- Changed visual states have probe coverage and reviewed light/High Contrast
  screenshots; real keyboard walkthrough, Mac checks and Linux Orca CI are
  recorded separately. No screenshot means no visual-pass claim.

## Boundaries and remaining onboarding evidence

Real browser sign-in has not yet been recorded as verified. Include successful
sign-in and recheck in end-to-end history validation wherever authentication is
required; backend work with synthetic responses can proceed independently.
The later conversion of status notices to native OK dialogs also still needs
live screen-reader confirmation. Existing manual menu-flow passes remain valid
for the paths actually tested.

Owned Muse chat creation, streaming replies, cancellation, permissions, model
selection, attachments and file changes are deferred to the execution milestone.
That milestone must verify native permission enforcement before enabling sends.

## Starting implementation locations

- `thechatplace/providers/base.py`: discovery and reader contracts/capabilities.
- `thechatplace/providers/discovery.py`: provider isolation and stale snapshots.
- `thechatplace/session_controller.py`: routing and identity/ownership boundary.
- `thechatplace/sessions.py`: listed versus runtime providers and labels.
- `thechatplace/muse_account.py`: existing account-only connection boundary.
- `thechatplace/platform_paths.py`: native executable and process management.
- `thechatplace/ui/main_frame.py`: provider setup and existing conversation UI.

## Implementation and protocol evidence

`providers/muse_connection.py` owns a lazy, read-only host with a method
allowlist containing only session/list and session/read. AccountConnection's
separate allowlist remains unchanged. Native executable/version verification
precedes serve startup. MSP envelope schema 1 and both fingerprints must match
one of the installed, verified Muse builds (`VERIFIED_SCHEMAS` in
`providers/muse_connection.py`):

- 1.4.18 stable: `sha256:77dda18550adef14e257b015fe4e6490b722723f7d526df5ba4867939fff68a3`.
- 1.4.18 experimental: `sha256:43a76702a69cdfe355db9edac2c7efdc8f75ae40daaae28789ad1d6ec68c3603`.
- 1.4.20 stable: `sha256:283b1b125dcc9172928fb5d6711a92a353796d030a7c186988ed527a13cf07f0`.
- 1.4.20 experimental: `sha256:c2f9255c3e027d8c167a64f638429655f2121d5b28d7b5f6b5da437a99dbe57a`.

A connected host keeps its first catalog, so `MuseProvider.collect()` rotates
the host (`refresh_catalog()`) before each `session/list`: a conversation saved
by another process while the host is open would otherwise stay invisible until
restart. The rotation waits for any in-flight history read before closing the
old host.

Native verification on 2026-11-13 succeeded while signed out: session/list
returned an empty catalog, and session/read for a nonexistent valid synthetic
UUID returned sessionNotFound (-32020). No turn, session creation, trust request
or sign-in was initiated in that check. On 2026-11-14, a separate isolated
echo-provider fixture verified native persistence, discovery of one conversation
and reading of its two messages, with zero unreadable items. The host's museHome
was verified inside the temporary profile before fixture creation. No user Meta
account or history was used. On 2026-11-14 the 1.4.20 fingerprints above were
verified natively: the updated provider discovered a real user-created session
by ID and its metadata read succeeded. See the validation guide for the
distinction from manual screen-reader and real Meta-provider testing.

The official method references explicitly describe both operations as read-only
and not attaching to a session:

- [Session listing](https://meta-models.github.io/muse-code-sdk/next/generated/msp/methods/session-list/).
- [Session reading](https://meta-models.github.io/muse-code-sdk/next/generated/msp/methods/session-read/).
- [History envelope](https://meta-models.github.io/muse-code-sdk/next/generated/msp/types/sessionhistory/).
- [Item fields](https://meta-models.github.io/muse-code-sdk/next/generated/msp/types/item/).
- [Snapshot fields](https://meta-models.github.io/muse-code-sdk/next/generated/msp/types/viewsnapshot/).

`providers/muse.py` pages the catalog with cursor-loop, page-count and elapsed-time
limits, qualifies session identity by provider, normalizes titles and reads
history into the existing Transcript model. Metadata never grants ownership or
fresh live state. Retracted content is omitted, item revisions preserve first
appearance order, and unsupported or truncated items produce a persistent
visible warning. Conversation history stays untouched on a failed refresh.

History accepts inline item arrays and complete snapshots with supported
snapshot schema 1 and a matching view cursor. Snapshot schema version is checked
independently of the MSP envelope version. Anchored snapshots, unsupported
snapshot versions and no-history envelopes are reported as unavailable rather
than silently showing a partial or empty chat. Recovering those formats through
view/page is a future extension; it must not introduce a resume fallback.
Tool output is the transcript-visible output Muse supplies; truncated or unknown
content is identified, rather than fetching arbitrary output references.

The window registers the lazy provider when Muse is found, offers a Muse Code
Chats filter, and reuses the normal read-only conversation view. Muse is a listed
provider, never a runtime provider. The first history-read failure is announced
with a persistent safe status; repeated failures do not repeat the announcement.
Loaded messages and focus are preserved. Probe surfaces and Orca steps use
synthetic data. The four packaging entry points include Muse modules explicitly,
and the app's smoke check imports them without starting a host.

See [manual history validation](../testing/muse-code-history.md). A populated
conversation from the user's Meta account, the new history flow with NVDA/JAWS, light/High Contrast VM pictures,
Mac runtime and Linux Orca CI remain separate validation work. No VM tooling is
available in this environment; no visual baseline was accepted or desktop theme
changed. Existing onboarding reader passes do not establish a history-flow pass.

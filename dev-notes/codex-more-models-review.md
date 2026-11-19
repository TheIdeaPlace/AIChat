# Codex model review fixes (#685)

Reviewed November 19, 2026. No real key was loaded for these review checks.

## Model pages checked individually

Each page explicitly lists maximum input 922,000 and maximum output 128,000:

- [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol.md)
- [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra.md)
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna.md)
- [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra.md)
- [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol.md)
- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna.md)
- [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol.md)

GPT-6.1 Sol explicitly lists $2 input and $0.10 cached input per million tokens.
Its 5% cache rate was correct; no price change was necessary.

Sol's promotion says "until November 30, 2026", without a time or zone. The cutoff
at November 29, 10:00 UTC is our conservative inference: the earliest start of
November 30 anywhere (UTC+14). It is not a published precise deadline.

## Native service tier remains unresolved (review item 15)

Codex CLI 0.163.0 was run against a local synthetic Responses endpoint using a
made-up key and isolated temporary homes. Both explicit standard on the thread
with no turn override, and Priority on the thread with a standard turn override,
omitted `service_tier` from the outbound request. Explicit process config and
`fast_mode=true` did not change that. No paid request was made.

The versioned [native model resolver](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/protocol/src/openai_models.rs)
filters `default` unconditionally in `service_tier_for_request`. This is later than
both lifecycle and turn configuration. The app-server's accepted settings cannot
prove a wire-level standard pin. The [Responses API definition](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_create_params.py)
says omission defaults to `auto`, which can use the project's configured tier.

Our standard settings and refusal of explicit Priority remain in place, but are
not a guarantee for all OpenAI projects. A paid nano response saying `default`
would only establish the current project's behavior; it would not fix this gap.
A temporary refusal of all API-credit sends was proposed, but automatic approval
review rejected that consequential availability change without explicit user approval.
That decision is pending; the refusal has not been applied. Current sends remain enabled.
Shipping needs an upstream Codex fix exposing an explicit standard wire value,
or a separately reviewed transport change. Do not claim item 15 is fixed.

## Other review findings

1. Backend consent binds connection, thread, model and tier; expiry queues return unsent with a spoken reason.
2. Expiry continues the active turn, preserves usage updates and its allowance, and stops dollar-based interruption.
3. Incomplete tokens say "at least"; stopped/failed verbs match the outcome; unchanged late counts are not repeated.
4. The expiry clock is UTC with the conservative cutoff explained above.
5. Release Held says unknown cost may exceed an unpriced allowance and restores the inactive-total wording.
6. The question uses the model reported by the verified runtime, including chats with no saved model.
7. Cancel is created and laid out before Send; Enter on the warning cancels.
8. Probes use a real ledger and production Properties/Usage/status formatters; status omits token details.
9. The 5% cached-input rate is confirmed above.
10. Each model's own limits are confirmed above.
11. Empty effort lists are retained by both catalog parsing and API filtering.
12. Unknown-only spending leads with "cost unknown"; mixed spending retains unknown priced holds.
13. A frozen priced token prefix is subtracted from later unpriced snapshots, including overlap corrections.
14. README, guide, generated docs, short release points and Orca paths cover restart, steers and ultra exclusion.

## Validation

391 affected windowless regression tests passed. The free real-CLI suite passed four
checks and skipped seven paid checks; this review spent $0. Window tests and probe/Orca runs
were not run on Kelly's PC. The new paths are in the existing CI/probe surfaces:
`openai-unpriced-confirm`, `openai-keys-held`, `openai-properties`, `openai-usage`,
and `main-openai-unpriced`. In the confirmation, listen for the warning, Tab to
Cancel, Tab to Send on API credits, Shift+Tab back to Cancel, and Escape. Reopen
and press Enter on the warning: it must cancel and keep the message.

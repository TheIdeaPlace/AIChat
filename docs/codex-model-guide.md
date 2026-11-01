# Choosing a model in Codex

Last checked against official OpenAI documentation: 1 November 2026.

This is a general guide to choosing Codex models for coding, writing, research, planning and everyday work. Its recommendations are practical starting points, not measured benchmarks. Model availability and settings can change.

## Start here

For most work, start with **GPT-6.1 Sol at medium reasoning**, if available. Choose **GPT-6 Luna** for straightforward tasks where speed and usage matter. Choose **GPT-6 Astra** for demanding analysis, ambiguous problems or work where a mistake would require substantial rework.

OpenAI's [model selection guidance](https://developers.openai.com/api/docs/guides/model-selection) positions Luna for efficiency, Sol for balancing capability and cost, and Astra for the most demanding work. The examples below apply those distinctions to common tasks.

## What the different models are for

### GPT-6 Astra — demanding work

Model ID: `gpt-6-astra`.

Astra is OpenAI's most capable general model. Consider it when a task requires broad context, difficult reasoning or judgment across competing requirements. See [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra).

Good starting uses include diagnosing a stubborn bug, evaluating architectural alternatives, synthesizing conflicting research, or reviewing a complex proposal. Start at medium reasoning and increase it when the analysis needs more depth. You can also choose Astra as your default if capability matters more than usage cost.

### GPT-6.1 Sol — a balanced everyday choice

Model ID: `gpt-6.1-sol`.

OpenAI describes GPT-6.1 Sol as offering near-Astra performance at lower cost. It is the current recommended Sol model for complex Codex workflows. See [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Use it for implementing features, investigating ordinary bugs, producing substantial documents, organizing research or completing work that involves several connected steps. Medium reasoning is a useful starting point; try high when the task has more uncertainty or interacting constraints.

### GPT-6 Sol — the preceding Sol model

Model ID: `gpt-6-sol`.

GPT-6 Sol supports complex coding and agent workflows. GPT-6.1 Sol is the newer Sol model. See [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol).

It remains worth understanding if it appears in your picker or an existing workflow already uses it successfully. For a new task, start with GPT-6.1 Sol when available. For established work, compare results before changing a working setup simply because a newer name appears.

### GPT-6.1 Terra — fast, everyday work

Model ID: `gpt-6.1-terra`.

OpenAI describes GPT-6.1 Terra as a fast, efficient model for clear writing, visual understanding and computer use. It needs Codex CLI 0.159.0 or later. See [Codex models](https://learn.chatgpt.com/docs/models).

Try it for drafting and editing prose, describing screenshots or images, and routine tasks that need a little more than Luna but not Sol's depth. Move to Sol when the work involves several connected decisions.

### GPT-6 Luna — focused, efficient work

Model ID: `gpt-6-luna`.

Luna is designed for efficient, focused work at high volume. See [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

Try it for summarizing a supplied document, extracting information, rewriting a short passage, fixing an isolated issue or making a clearly specified edit. Use low reasoning for simple transformations and medium when a bounded task still requires judgment. Luna supports Max effort but not Ultra. Move to Sol when the task grows into several interacting decisions.

### The GPT-5.6 models — an earlier family

Codex's model page no longer lists the GPT-5.6 models; OpenAI still offers them in the API, where they're the published replacements for the GPT-5.x-Codex models removed in July 2026. If one appears in your picker or an existing workflow, the notes below say what each was for.

#### GPT-5.6 Sol — an earlier flagship option

Model ID: `gpt-5.6-sol`.

GPT-5.6 Sol is the flagship of the GPT-5.6 family, designed for complex professional work. The API alias `gpt-5.6` routes to this model. See [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

Consider it when newer models are unavailable or when you have an established workflow whose results you already trust. Assess it on the same task before replacing it; generation names alone do not establish which result meets your needs.

#### GPT-5.6 Terra — a balance within the earlier family

Model ID: `gpt-5.6-terra`.

Terra balances capability and cost within the GPT-5.6 family and roughly corresponds to the earlier mini tier. See [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra).

It is an option for routine work when offered, particularly if you are choosing among GPT-5.6 models. Compare it with current Luna and Sol on your actual task rather than assuming an older middle tier has the best current value.

#### GPT-5.6 Luna — the earlier efficient option

Model ID: `gpt-5.6-luna`.

GPT-5.6 Luna targets cost-sensitive, high-volume work and roughly corresponds to the earlier nano tier. See [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

It can be useful for established extraction, summarization and other narrowly defined workflows. For new work, try GPT-6 Luna first when available, then compare quality and usage if you have a reason to keep the earlier model.

## Match the model to the task

These are suggested starting points, not guarantees about speed or correctness.

| Task | Start with | Reasoning |
|---|---|---|
| Reword an email or tidy a short document | GPT-6 Luna | Low |
| Write or edit a page of prose, or describe a screenshot | GPT-6.1 Terra | Low or medium |
| Summarize supplied meeting notes or extract action items | GPT-6 Luna | Low or medium |
| Explain a small piece of code or make a specified edit | GPT-6 Luna | Medium |
| Draft a report, plan a project or implement a feature | GPT-6.1 Sol | Medium |
| Research a question and compare several sources | GPT-6.1 Sol | Medium or high |
| Debug a problem spanning several components | GPT-6.1 Sol | High |
| Resolve conflicting evidence or evaluate difficult tradeoffs | GPT-6 Astra | Medium or high |
| Investigate a persistent failure or review a consequential design | GPT-6 Astra | High; consider extra high if needed |

A short request may require deep judgment. A large batch of repetitive work may still suit Luna. Choose according to the decisions involved and the consequences of errors.

## Reasoning effort is a separate choice

The model determines which AI performs the work. Reasoning effort adjusts how much deliberation it uses. Higher effort generally takes longer and uses more tokens. Supported settings and display names vary; use the options your client offers. See [model and reasoning controls](https://learn.chatgpt.com/docs/models#choose-a-model).

| Effort | A useful starting use |
|---|---|
| None, where offered | Simple transformations with little analysis. |
| Low or Light | Clear instructions and straightforward edits. |
| Medium | Everyday work requiring judgment and several steps. |
| High | Difficult debugging, detailed review or interacting requirements. |
| Extra high (`xhigh`) | A specific unresolved problem requiring deeper analysis. |
| Max, where offered | Especially demanding work where additional time and usage are acceptable. |

These descriptions are practical guidance, not an exact mapping of every client's labels. **Ultra** is different: OpenAI documents it as using subagents for complex work, rather than simply increasing a single agent's reasoning. See [Codex models](https://learn.chatgpt.com/docs/models).

Start with the default or medium setting. If the result misses important constraints, supply the missing context and try higher effort. If it repeatedly misunderstands the problem, try a stronger model. Increasing effort cannot supply missing files, current sources or a reproducible example.

## How to select a model

In an interactive Codex CLI session, use `/model` to select a model and supported reasoning effort. You can also choose a model when starting Codex:

```text
codex --model gpt-6.1-sol
codex --model gpt-6.1-terra
codex --model gpt-6-luna
codex --model gpt-6-astra
```

In the desktop app or IDE extension, use the model control beneath the composer. See the official [selection instructions](https://learn.chatgpt.com/docs/models#choose-a-model).

Availability depends on your plan, sign-in method, client, rollout and administrator settings. An API catalog entry does not guarantee a model appears in your Codex picker. Selecting a model does not grant access to it. See [current Codex availability](https://learn.chatgpt.com/docs/models).

## Older names and retirements

Older tutorials may recommend models with “Codex” in their names. That suffix alone does not make them the current recommended choice.

| Older model | Status as of this guide's date |
|---|---|
| GPT-5.5 | Retired from Codex with ChatGPT sign-in on 14 October 2026. The replacement is GPT-6.1 Sol on Plus, Pro, Business, Enterprise and Edu, and GPT-6 Luna on Free and Go. That retirement does not affect API-key access. |
| GPT-5.4 and GPT-5.4-mini | Retired from Codex with ChatGPT sign-in on 31 August 2026; replaced by GPT-6 Sol and GPT-6 Luna. |
| GPT-5.3-Codex-Spark | Retired on 14 September 2026. |
| GPT-5.3-Codex | Deprecated in Codex with ChatGPT sign-in, and in the API; scheduled removal on 1 April 2027. The published replacement is GPT-6 Sol. |
| GPT-5-Codex, GPT-5.1-Codex, GPT-5.1-Codex-Max, GPT-5.1-Codex-Mini and GPT-5.2-Codex | Removed from the API on 23 July 2026. The published replacement is GPT-5.6 Sol, or GPT-5.6 Terra for GPT-5.1-Codex-Mini. |

The GPT-5.5 status comes from [Codex models](https://learn.chatgpt.com/docs/models#gpt-55-retirement). API statuses come from [OpenAI's deprecation schedule](https://developers.openai.com/api/docs/deprecations). Product retirement and API retirement are separate events; check the one that matches how you sign in.

## Get good value from your choice

Give the model a clear goal, the relevant material and a way to judge success. For example: “Summarize this report for a nontechnical reader in 300 words, preserve its uncertainties, and identify claims that need verification.” For a code change, include expected behavior and the checks that should pass.

Compare models using the same input and acceptance criteria. Count corrections and review time as part of the cost. Keep the lighter setting when it consistently produces an acceptable result; use a stronger model when it reduces substantial rework. This approach follows OpenAI's [model selection guidance](https://developers.openai.com/api/docs/guides/model-selection).

Subscription credits and API token prices are different billing systems. Check the current [Codex plan and usage information](https://learn.chatgpt.com/docs/pricing) or [API pricing](https://developers.openai.com/api/docs/pricing) for the service you use. This guide does not promise a fixed number of tasks or a fixed cost per model.

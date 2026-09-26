# Sentence Agent · 地道句子本

**A macOS desktop agent that helps Chinese speakers say things the way native English speakers do. Built on the Claude API, and it works with any OpenAI-compatible provider or API relay too.**

[中文说明](README.zh-CN.md)

Type any sentence, in Chinese or English. The agent works out what you need: natural English for something you want to say, a breakdown of an English sentence you don't quite get, or a check of English you wrote yourself. The answer comes back as a rich card, and **every sentence is saved automatically** into a bilingual notebook. You review it the way you'd review vocabulary: cover one side, flip one sentence at a time, and let spaced repetition decide what comes back when.

---

## What it does

| You send | The agent gives you |
|---|---|
| **A Chinese sentence**<br>这事儿我得再考虑考虑。 | 2–4 natural English versions in different registers (casual / neutral / formal), each with a note on when to use it; phrases worth keeping, with new example sentences; common Chinglish renderings to avoid, and why. |
| **An English sentence you want to understand**<br>*I'd rather not get into it right now.* | A natural Chinese translation; the sentence split into color-coded parts (subject, verb, object, complements, adverbials, clauses…); the sentence skeleton and pattern; what it really means (tone, subtext, when people say it); grammar points with examples. |
| **English you wrote yourself**<br>*I very like this movie, it make me cry. Is this right?* | A verdict (natural / understandable but not idiomatic / incorrect), a word-level diff of the minimal correction, an issue-by-issue explanation (grammar, word choice, collocation, tone…), and how a native speaker would actually put it. |
| **Anything else** | Direct answers and follow-ups: "What's the difference between *hang out* and *hang around*?", "Can I use that in a work email?", "What was that sentence about 'considering' I asked last week?" |

And around the conversation:

- **Notebook.** Chinese on the left, English on the right. Hide either column and tap a single sentence to reveal it. Search, filter (due / still shaky / learned), shuffle, edit, or export to CSV.
- **Flashcard review.** Practise Chinese → English or English → Chinese. "Got it" pushes a card out 1, 3, 7, 16, then 35 days; "Not yet" brings it back later in the same round and again tomorrow.
- **Long-term memory.** When the same kind of mistake keeps showing up (articles, tense, *very + verb*…), the agent records it as a weak point and takes it into account in future conversations.
- **Any model provider.** Use the official Anthropic API, OpenAI, DeepSeek, Qwen, Kimi, GLM, SiliconFlow, OpenRouter, Gemini, or any relay that speaks the OpenAI or Anthropic format. Configure several and switch from the chat box; a conversation can move between providers mid-way.
- **Native voice.** Any English sentence can be read aloud with the macOS system voice.
- **Cost awareness.** Settings shows an estimated spend for today and this month. The app also suggests starting a new conversation once the current one gets long enough to be expensive.

## How the agent works

```mermaid
flowchart LR
    U([You]) -->|message| S[Agent session]
    S -->|"streamed request<br/>system prompt + history + tools"| C[(Claude API)]
    C -->|"thinking · text · tool calls<br/>(streamed)"| S
    S -->|tool call| T{{Tools}}
    T -->|result| S
    T <--> DB[(SQLite<br/>notebook · history · memory)]
    S -->|"live events<br/>cards render as they are written"| UI[Desktop window]
```

**The loop.** Each turn is an agent loop: call the model, run whichever tools it asks for, feed the results back, repeat until it answers, with a cap of 10 tool rounds per turn. There are two engines behind one interface:

- **Anthropic engine** runs on the tool runner in the official Anthropic Python SDK (`client.beta.messages.tool_runner`, streaming). The default is **Claude Opus 5** with adaptive thinking (summarized thinking appears in a collapsible block) and effort set to *medium*.
- **OpenAI-compatible engine** runs on the official OpenAI Python SDK's streaming Chat Completions with function calling. It tolerates what varies between compatible endpoints: providers that reject `stream_options`, reasoning streamed in non-standard fields (DeepSeek's `reasoning_content`, OpenRouter's `reasoning`), and missing tool-call ids.

**The model decides what to do.** Nothing in the app routes your input by keyword. The model reads the message and picks a tool:

| Tool | When Claude calls it | What happens |
|---|---|---|
| `save_translation` | You gave a Chinese sentence to express in English | Saves the card; the notebook keeps the recommended version |
| `save_analysis` | You gave an English sentence to understand | Saves the translation and structural breakdown |
| `save_correction` | You gave your own English to check | Saves the correction; the notebook keeps the natural version |
| `search_notebook` | You ask about something you looked up before | Keyword search over your notebook |
| `remember_weak_point` | A mistake keeps recurring | Adds or increments a weak point in long-term memory |
| `get_study_overview` | You ask about your progress | Notebook totals, cards due, recorded weak points |

**Tool inputs are the UI.** The three `save_*` tools use typed Pydantic schemas, and their arguments *are* the card content. Tool-call arguments are parsed as partial JSON while they stream (`eager_input_streaming` on the official Anthropic API, incremental function-call arguments elsewhere), so the card appears while the model is still writing it. When the tool executes, the input is validated and the sentence lands in the notebook. If an input fails validation or isn't valid JSON, the error goes back to the model so it can retry.

**One history, any provider.** Every conversation is stored append-only in SQLite in a single canonical format (Anthropic-style content blocks: text, thinking with its signature, `tool_use` / `tool_result`). Reasoning from other providers is kept as a display-only block. Before each request the history is translated for whichever provider is active: tool calls become `tool_calls` and `role: "tool"` messages for OpenAI-format endpoints, and Claude's thinking signatures go back only to the official Anthropic API. That is why conversations survive restarts and can switch providers midway. If a turn is stopped or the app is closed mid-tool-call, the dangling call is closed with an error result, so the history is always valid.

**Relays get the safe request shape.** Relays and proxies often reject fields they don't recognize. So beta features (adaptive thinking, effort, prompt-cache controls, eager input streaming, refusal fallbacks) are sent only to the official Anthropic API with a known Claude model. Everything else gets a plain request. For OpenAI-format endpoints, tool schemas are reduced to a portable JSON Schema subset (no `anyOf`/`null`, `title` or `additionalProperties`), which strict compatibility layers such as Gemini's accept.

**Memory without cache misses.** The system prompt has two blocks. The first is a fixed instruction block with a cache breakpoint, shared by every conversation. The second is a learner profile (today's date, notebook size, recorded weak points) that is snapshotted when a conversation starts and stays fixed for that conversation. Together with automatic caching of the conversation tail, most of each request is served from the prompt cache.

**Resilience.** On the official API, Opus 5 and Fable 5.1 requests enable server-side refusal fallbacks (`fallbacks: "default"`). API errors from either SDK (bad key, rate limits, insufficient balance, network, wrong model or base URL, a model without tool support) are mapped to plain-language messages, with a shortcut to Settings where that helps.

## Architecture

```
Desktop window  (pywebview, native WebKit)
  └─ UI: src/sentence_agent/static        plain ES modules, no build step
        │  HTTP + server-sent events; bound to 127.0.0.1, per-launch access token
Local server  (FastAPI)                   src/sentence_agent/server.py
  ├─ agent/session.py           one turn: load history, pick the engine, keep the history valid
  ├─ agent/anthropic_engine.py  Anthropic Messages via the SDK tool runner (official API or relay)
  ├─ agent/openai_engine.py     OpenAI-compatible Chat Completions with function calling
  ├─ agent/history.py           canonical history → Anthropic / OpenAI request formats
  ├─ agent/tools.py             the six tools and their schemas
  ├─ agent/prompts.py           system prompt + learner profile
  ├─ agent/transcript.py        stored history → chat view
  ├─ providers.py               provider configs, presets, model listing
  ├─ store.py                   SQLite: conversations, messages, cards, reviews, memory, usage
  ├─ srs.py                     spaced-repetition scheduling
  └─ credentials.py             one API key per provider in the macOS Keychain
```

## Getting started

**Requirements:** macOS 12 or later, Python 3.10+, and an API key from any supported provider: an [Anthropic API key](https://console.anthropic.com), an OpenAI-compatible provider's key, or a relay's key. API usage is billed separately from chat subscriptions; looking up one sentence typically costs a few cents or less.

```bash
git clone git@github.com:wangshen-tech/sentence_agent.git
cd sentence_agent
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m sentence_agent     # run from source in a native window
scripts/build_app.sh                   # or build dist/地道句子本.app
```

On first launch, open **Settings**:

- **Anthropic:** edit the built-in *Anthropic 官方* provider and paste your key. The `ANTHROPIC_API_KEY` environment variable also works.
- **Anything else:** click **Add provider**, pick a preset (OpenAI, DeepSeek, Qwen, Kimi, GLM, SiliconFlow, OpenRouter, Gemini) or *relay*, and choose the interface format. Fill in the base URL and key, then press **Test connection** to fetch the endpoint's model list and pick a model.

Keys are stored in the macOS Keychain, one per provider, and never written to disk in plain text. The model you pick must support tool calling (function calling), because that is how sentences get saved to the notebook. Switch providers any time from the selector under the chat box.

Keyboard: <kbd>Return</kbd> send · <kbd>Shift</kbd>+<kbd>Return</kbd> new line · <kbd>⌘</kbd><kbd>N</kbd> new conversation · in review, <kbd>Space</kbd> flip, <kbd>1</kbd> not yet, <kbd>2</kbd> got it.

## Development

```bash
.venv/bin/python -m pytest              # offline tests, no API key needed
.venv/bin/python scripts/dev_server.py  # scripted fake Claude + demo data → http://127.0.0.1:8765/#t=dev
```

The agent tests don't mock the agent. They replace the HTTP layer with scripted Anthropic Messages and OpenAI Chat Completions endpoints that replay real server-sent event streams (thinking and reasoning, streamed tool arguments, tool calls, refusals, auth errors). The **real SDK tool runner and OpenAI client, real tools and a real SQLite database** run end to end. The tests cover:

- request shape per provider type: official, relay, OpenAI-compatible
- history round-tripping with thinking signatures
- switching providers mid-conversation
- invalid tool input
- endpoints that reject `stream_options`
- refusals
- repair of interrupted tool calls

## Privacy

Your notebook, conversations and memory live only on your Mac, in `~/Library/Application Support/SentenceAgent/`. API keys live in the Keychain. The only network traffic is to the provider you configured and chose. The local server listens on `127.0.0.1` only and rejects any request that doesn't carry the token generated at launch.

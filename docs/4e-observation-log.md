# Pikina OS — Phase 4e Observation Log (Post-Fix Run)

Tier 1 handles deterministic matches. Tier 2 (Ollama/llama3) handles fallbacks.

| # | Input Query | Tier | Tool | Outcome | Latency |
|---|---|---|---|---|---|
| 1 | `open notepad` | T1 | `—` | ✅ ok | 0.0s |
| 2 | `start the calculator` | T1 | `—` | ✅ ok | 0.0s |
| 3 | `look up files with resume in the name` | T1 | `—` | ✅ ok | 0.2s |
| 4 | `add event college project meeting on July 20th at 3pm` | T1 | `—` | ✅ ok | 0.5s |
| 5 | `add a task to call mom by tonight` | T2 | `—` | ✅ ok | 2.5s |
| 6 | `mark task call mom as done` | T2 | `—` | ❌ error: timeout | 26.6s |
| 7 | `recall what i did yesterday` | T1 | `—` | ✅ success | 0.1s |
| 8 | `list all pending tasks` | T2 | `—` | ❌ error: timeout | 17.2s |
| 9 | `quiet hours from 22:00 to 08:00` | T1 | `—` | ✅ ok | 0.1s |
| 10 | `be formal` | T1 | `—` | ✅ ok | 0.1s |
| 11 | `calculate 45 * 2 + 10` | T1 | `—` | ✅ ok | 0.0s |
| 12 | `start the stopwatch` | T1 | `—` | ✅ ok | 0.0s |
| 13 | `reset the stopwatch` | T1 | `—` | ✅ ok | 0.0s |
| 14 | `can you launch something to write some text` | T2 | `—` | ❌ error: timeout | 17.3s |
| 15 | `where is my presentation pdf` | T2 | `—` | ✅ ok | 12.4s |
| 16 | `tell me a joke` | T2 | `—` | ❌ no_match:  | 8.9s |

## Summary

- **Total queries:** 16
- **Tier 1 hits:** 10
- **Tier 2 invocations:** 6
- **Passed:** 12
- **Failed:** 4
- **Pass rate:** 75%

## Remaining Failures

- **mark task call mom as done** → `timeout`
- **list all pending tasks** → `timeout`
- **can you launch something to write some text** → `timeout`
- **tell me a joke** → ``

## Future Enhancements & Scaling Refinements (Part F)

1. **Miscategorization Risk in Tier 2:** While Tier 1 regex matches successfully intercept specific phrasings (like `"quiet hours from 22:00 to 08:00"`), slightly modified phrasing (e.g. `"set my quiet hours"`) will fall through to Tier 2. The parameter schema prompt inclusion reduces wrong-tool selections by clarifying param expectations, but generative miscategorization remains a minor risk to monitor.
2. **Registry Scaling & Relevance Filtering:** At ~48 tokens per capability schema, growing the registry past 40 tools will blow the prompt budget. We will implement a relevance filter (keyword-matching / vector similarity) in Part F to dynamically select the top 10 most candidate capabilities, keeping the list block under 500 tokens.
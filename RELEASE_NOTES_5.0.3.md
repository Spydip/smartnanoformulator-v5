# SmartNanoFormulator v5.0.3

## Cloud AI and Render changes

- Adds OpenRouter as a cloud AI provider for RAG explanations and no-evidence exploratory hypotheses.
- Defaults to `openrouter/free`; backend rejects models unless the ID is `openrouter/free` or ends in `:free`.
- Stores the API key server-side (`OPENROUTER_API_KEY`). No model key is embedded in browser code or the ZIP.
- Updates Render Blueprint to enable free cloud AI and ask for the OpenRouter key and shared app password as secrets.
- Keeps the Render Free service and ephemeral seeded SQLite model; documentation explains loss of runtime history after spin-down/restart/redeploy.
- Updates the UI help text from local Ollama setup to server-side cloud AI setup.
- Moves the local Windows launcher to port 8005 and waits for the exact v5.0.3 health response, avoiding the legacy v2/v3 app on port 8000.

## Verification performed

- 117 unit tests pass, including OpenRouter free-model configuration, zero-cost accounting, authorization header and rejection of paid model IDs.
- Python compilation passes.
- Local server health endpoint returns `{"ok": true, "app": "SmartNanoFormulator", "version": "5.0.3"}` on port 8005.
- ZIP integrity is checked at packaging time.
- No real OpenRouter request was made because no user API key was supplied; Render deployment was not performed.

## Limitations

- OpenRouter free-model availability and limits can change. Its currently published free-variant quota is 50 requests/day per account, with a 20 requests/minute rate limit.
- Render Free filesystems are ephemeral. Saved analyses, user imports, and laboratory history may be lost after sleep, restart, or redeploy. Export important results.
- RAG retrieval in this release is structured/lexical against bundled data; vector embedding-based semantic retrieval is not yet included.

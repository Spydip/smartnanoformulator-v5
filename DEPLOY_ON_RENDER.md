# SmartNanoFormulator v5.0.3 — free Render + free cloud AI

This package is configured for a **Render Free web service** and **OpenRouter's free-model router**. No Ollama installation or local model download is required. Render hosts the app; OpenRouter hosts the language model. You must create and add your own OpenRouter API key as a Render secret.

## What is free, and what is not

- Render Free web service: $0; it sleeps after 15 minutes without traffic and may take about a minute to wake.
- OpenRouter `openrouter/free`: routes requests to available free models. Current published free-variant limits are 20 requests/minute and 50 requests/day per OpenRouter account. Provider availability and limits can change.
- The application is configured for 12 LLM requests/day as an additional usage guard. This app-side SQLite ledger is ephemeral on Render Free and can reset after sleep/restart; the provider's own limit is the final external guard.
- No paid model is allowed by this configuration: the backend accepts only `openrouter/free` or a model ID ending in `:free`.
- This does not guarantee permanent free availability or uninterrupted model service. Do not add credits or select a paid model if you want to stay at $0.

## Deploy steps

1. Create a free account at GitHub and create a **private** repository for the project.
2. Extract this project folder and upload its contents to the repository root. Include `render.yaml`, `Dockerfile`, `start.sh`, `smartnano.db`, the `data_src` folder, and the rest of the app.
3. Create an OpenRouter account and generate an API key. Use its free model router; do not buy credits or select a paid model. Store the key securely.
4. In Render, choose **New → Blueprint**, connect the private repository, and review the Blueprint. Confirm the service plan is **Free**.
5. Render will ask for the two secret values:
   - `APP_ACCESS_PASSWORD`: choose a unique password of at least 16 characters.
   - `OPENROUTER_API_KEY`: paste your OpenRouter key.
6. Apply the Blueprint. Wait for the service to become Live.
7. Open `https://<your-service>.onrender.com/api/health`; it should return `ok: true` and version `5.0.3`.
8. Open the service URL and sign in with username `mates` and the password you supplied.
9. Open AI Research Assistant. Its status should show the configured free cloud model. Try a short question first.

## Important Render Free limitations

- Free web services spin down after 15 minutes idle and can take about a minute to wake.
- The filesystem is ephemeral. The app copies the bundled database seed to `/tmp` on startup. Saved analyses, imported rows, history and local model artifacts can disappear after a spin-down, restart or deploy.
- Free web services cannot attach persistent disks. Free Render Postgres exists but expires after 30 days, so it is not a durable long-term history solution.
- Use this as a class/demo workspace, not as the only storage location for real research results. Keep source data and exported reports outside Render.
- This uses one shared Basic Auth login, not separate accounts. Everyone with the shared password can see the same workspace and its saved records.

## Privacy and research integrity

Prompts and the retrieved excerpts sent to OpenRouter leave the Render server and are processed by the selected model provider. Do not submit confidential, unpublished or sensitive research material unless the provider's current terms are acceptable to your group. API credentials remain in Render environment secrets and are never placed in the browser.

RAG answers must cite retrieved records. When no suitable records are retrieved, the model may create a clearly labelled research hypothesis, but it must not invent citations, measurements or validation. AI-generated compositions are hypotheses requiring laboratory review.

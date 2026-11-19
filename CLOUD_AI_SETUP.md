# Free cloud AI setup

For Render, follow `DEPLOY_ON_RENDER.md`. The app uses OpenRouter's `openrouter/free` model router. Create an API key with OpenRouter and add it only as the `OPENROUTER_API_KEY` secret in Render. Do not paste it into the browser or commit it to GitHub.

This avoids installing a local model or downloading gigabytes to your computer. OpenRouter currently documents a 50-requests/day limit for free model variants; limits and model availability can change. The app's own 12-per-day limit is an extra guard but may reset with Render's ephemeral filesystem.

On a local Windows computer, the app can still run without cloud AI. To enable cloud AI locally, copy `.env.example` to `.env`, add the OpenRouter API key, and keep `.env` private. `run_smartnano_v5.bat` launches this v5.0.3 package on `http://127.0.0.1:8005` so it does not conflict with old apps on port 8000.

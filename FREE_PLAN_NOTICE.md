# Render Free plan notice — SmartNanoFormulator v5

The included `render.yaml` selects Render's Free web-service plan and attaches no persistent disk. `start.sh` rebuilds the runtime database from the bundled seed at each start. Any changes to the database, imported data, analysis history, lab-loop records or local usage ledger can be lost when the service sleeps, restarts or redeploys.

Download the JSON archive from **Reports & History → Export history JSON** and store it outside Render before a restart. The archive is not automatically restored at startup; the app currently rebuilds from the packaged seed only. Keep valuable research data and source files somewhere you control.

Free services can sleep after 15 minutes without traffic and take time to wake. Hosted startup requires a unique `APP_ACCESS_PASSWORD` of at least 16 characters. The app uses one shared Basic Auth account. The optional LLM is off by default; its local spend ledger is ephemeral, so use provider-side spend limits if you intentionally enable it.

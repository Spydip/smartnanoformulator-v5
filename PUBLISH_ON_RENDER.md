# Publish SmartNanoFormulator v4.0.0 on Render Free

This repository is configured for a Render Free Docker web service. The Blueprint intentionally contains no persistent disk and no paid database.

## 1. GitHub

- Create a repository (private is recommended).
- Upload the *contents* of this project folder to the repository root.
- Verify `render.yaml` and `Dockerfile` are at the root.
- Do not commit `.env`, API keys or passwords.

## 2. Render

- Sign in at https://dashboard.render.com/ and connect GitHub.
- Choose **New -> Blueprint** and select the repository.
- Confirm `smartnanoformulator-v4-free` uses the **Free** plan. Do not upgrade.
- Provide a strong, unique `APP_ACCESS_PASSWORD`. Username defaults to `mates`.
- Click Apply and wait until the service is **Live**.

## 3. Free-tier behaviour

Render Free web services sleep after 15 minutes without inbound traffic and wake on the next request (often around one minute). Free web services have an ephemeral filesystem and cannot attach persistent disks. This build seeds `/tmp/smartnano.db` from the database included in the container. Changes saved to SQLite, uploaded datasets and local model artifacts may disappear when the service sleeps, restarts or redeploys. The service is therefore suitable for a public demonstration, not as the sole record store for actual experimental results. Keep experimental data backed up locally.

Render's free web services are subject to a 750-hour monthly workspace allowance and separate bandwidth/build-pipeline quotas. Review the Render Billing page. Do not select a paid plan or add optional paid services if you require $0 hosting.

## 4. Verify

Open the assigned `https://<service-name>.onrender.com` URL. The health endpoint is `/api/health`. Sign in with username `mates` and the password you configured. Render assigns the exact hostname after creation.

## 5. Optional external AI provider

The app works without an external language-model API key. Configuring one may incur separate provider charges; leave it unset for a strictly no-cost deployment.

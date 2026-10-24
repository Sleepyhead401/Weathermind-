# WeatherMind cloud GPS setup

The website and scheduled forecast engine are committed, but **background learning for new GPS locations requires a deployed registry**. The GitHub Pages website cannot store new locations on its own.

## One-time deployment

1. Create a Cloudflare account, create a Worker and a KV namespace, and bind the namespace as `LOCATIONS`.
2. Deploy `backend/worker.js` as the Worker. Configure a strong secret named `REGISTRY_ADMIN_TOKEN` in the Worker. Never paste that token into the dashboard or commit it to GitHub.
3. In the WeatherMind dashboard, tap **Cloud sync** and enter the deployed Worker's HTTPS URL. Tap **Use my location** to register the rounded area, only when you want to.
4. In the GitHub repository's Actions secrets, set `WEATHERMIND_REGISTRY_URL` to the Worker URL and `WEATHERMIND_REGISTRY_TOKEN` to the same admin token.
5. Configure the scheduled workflow to expose those two Actions secrets as environment variables to the `python engine.py` step. Without this step the engine continues to learn the default home location only.
6. Run the scheduled workflow, confirm `docs/data.json` updates, and inspect the dashboard comparison for the registered area.

**Privacy:** GPS areas are rounded to approximately 2 km. The registry is not a private user account system: a shared registry can collect locations submitted by anyone with its public URL. Use a private deployment or add authenticated registration before sharing the URL publicly. The dashboard's browser-local learning history remains device-specific and may be lost if browser data is cleared.

**Limits:** Cloudflare Worker and KV are external services with their own quotas and billing. NOAA coverage is primarily the United States. GitHub Actions scheduled jobs are not guaranteed to run at exact minute boundaries. Accuracy improvement is never guaranteed.

**Verification checklist:** Confirm that the Worker accepts a valid area, rejects invalid coordinates, requires the admin token to list areas, that the Actions job successfully fetches areas, and that the dashboard shows server-published forecasts and observations. No deployment or end-to-end run has been verified yet.

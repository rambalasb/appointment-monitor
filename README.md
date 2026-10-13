# Costco Tire Appointment Monitor 🚗

Checks Waitwhile booking pages for your configured Costco Tire locations in one run: it opens each URL, selects **Seasonal Exchange 4 Wheels/8 Tires** when needed, walks the calendar, and prints any available date/times it finds.

## Quick start

```bash
cd appointment-monitor
pip3 install -r requirements.txt
```

Copy `config.example.json` to `config.json` and set your `locations` (each needs a `name`, `url`, and optional `address`).

```bash
python3 monitor_fast.py
```

The script runs once through all locations and exits. Run it again anytime (e.g. from cron or a loop).

## Configuration

- **locations** — List of `{ "name", "url", "address" }` entries with your Waitwhile booking URLs.
- **check_interval_minutes** — Present in older configs; the fast monitor does not loop (ignore or remove).
- **max_parallel** (optional) — How many headless browsers to run at once. Defaults to one per location; lower it if RAM is tight.

All locations are checked at the same time, each in its own browser (about 30s for 7 locations). Each link is opened on its Select Services page (`.../locations/costcotire-01381/services/services?...`) and **Seasonal Exchange 4 Wheels/8 Tires** is clicked, so bare, `/time` and `/services/services` links all work. Don't rely on `/time` links on their own: Waitwhile ignores the `service=` parameter there and shows openings that aren't for the seasonal exchange. If Waitwhile says "No available times for the next …", that message is printed as is.
- **notifications** — When any location has open times, one alert is sent through each enabled channel:
  - `desktop` — macOS notification via `osascript`.
  - `email` — SMTP with STARTTLS (for Gmail, use an [App Password](https://myaccount.google.com/apppasswords)).
  - `webhook` — POSTs JSON with `text` (Slack) and `content` (Discord).

If one location fails to load, it is reported and the run continues with the rest. The browser is always closed when the run ends.

## Running on GitHub Actions

[.github/workflows/monitor.yml](.github/workflows/monitor.yml) runs the check every 10 minutes and emails you through Gmail when **new** openings appear, with a booking link for each location. Slots it has already alerted on are remembered between runs, so the same opening doesn't email you again.

- Config comes from the `CONFIG_JSON` repo secret: your `locations`, plus `notifications.email` with `"enabled": true`, `from_email`, `to_email` and `"password_env": "GMAIL_APP_PASSWORD"`. To update it: `gh secret set CONFIG_JSON < my-ci-config.json`.
- The Gmail [App Password](https://myaccount.google.com/apppasswords) is stored in its own secret: `gh secret set GMAIL_APP_PASSWORD` (paste it when prompted).
- Run it manually: **Actions → Costco Tire monitor → Run workflow**. Tick **Just send a test email** to check that email works.
- Locally, `python3 monitor_fast.py --test-alert` sends a test through every channel enabled in `config.json`.
- Other channels (`ntfy`, `webhook`, `desktop`) are still supported in config if you want them later.
- A run shows red only if every location fails to load (for example, if the site blocks the runner).
- GitHub pauses scheduled workflows after 60 days with no commits to the repo. Re-enable it from the Actions tab.

## Files

- `monitor_fast.py` — Entry script
- `config.json` — Your settings (gitignored if you use secrets)
- `config.example.json` — Template

## Security

If you store secrets in `config.json`, keep it private and do not commit it.

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

## Files

- `monitor_fast.py` — Entry script
- `config.json` — Your settings (gitignored if you use secrets)
- `config.example.json` — Template

## Security

If you store secrets in `config.json`, keep it private and do not commit it.

# Costco Tire Appointment Monitor 🚗

Automated monitoring solution for Costco Tire Center appointment availability on Waitwhile booking pages.

## Features

- ✅ **Multi-Location Monitoring** - Monitors 7 Calgary-area Costco locations simultaneously
- ⚡ **Automatic Page Monitoring** - Checks for appointment changes every 30 seconds
- 📅 **Date Display** - Shows all available appointment dates/times after each check
- 🔔 **Multiple Notification Methods** - Desktop notifications, Email, and Webhooks (Slack/Discord)
- 💾 **State Tracking** - Remembers previous states to detect new appointments per location
- 🔄 **Continuous Monitoring** - Runs 24/7 until you stop it
- ⚙️ **Configurable** - Easy JSON configuration for all settings

## Quick Start

### 1. Install Dependencies

```bash
cd appointment-monitor
pip3 install -r requirements.txt
```

### 2. Configure Settings

The `config.json` file is already set up with 7 Calgary-area Costco Tire locations. You can customize:

- **locations**: Add/remove locations to monitor
- **check_interval_minutes**: How often to check (currently set to 0.5 = 30 seconds)
- **notifications**: Enable/disable different notification methods

**Monitored Locations:**
1. S Calgary (Heritage Gate)
2. E Calgary (East Hills)
3. N Calgary (32 ST NE)
4. NW Calgary (Sarcee Trail)
5. Rocky View Calgary (CrossIron)
6. SW Calgary (Buffalo Run)
7. Okotoks

### 3. Run the Monitor

```bash
python3 monitor.py
```

That's it! The monitor will:
- Check all 7 locations every 30 seconds
- Display all available dates/times for each location after each check
- Send you a desktop notification (with location name) when appointments change at ANY location
- Send email alerts to rambala.sb@gmail.com with location details
- Keep running until you press `Ctrl+C`

## Notification Options

### Desktop Notifications (macOS) ✅ Enabled by Default

Desktop notifications work out of the box on macOS. You'll see native notifications when appointments are detected.

### Email Notifications 📧

To enable email notifications:

1. Edit `config.json`:
```json
"email": {
  "enabled": true,
  "smtp_server": "smtp.gmail.com",
  "smtp_port": 587,
  "from_email": "your-email@gmail.com",
  "to_email": "your-email@gmail.com",
  "password": "your-app-password"
}
```

2. For Gmail users:
   - Go to [Google App Passwords](https://myaccount.google.com/apppasswords)
   - Generate a new app password for "Mail"
   - Use that password in the config (not your regular Gmail password)

### Webhook Notifications 🌐

Perfect for Slack, Discord, or other services:

1. Get your webhook URL from your service:
   - **Slack**: Create an [Incoming Webhook](https://api.slack.com/messaging/webhooks)
   - **Discord**: Create a webhook in Server Settings → Integrations

2. Edit `config.json`:
```json
"webhook": {
  "enabled": true,
  "url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
}
```

## Configuration Reference

### config.json

```json
{
  "booking_url": "Your Costco Tire booking URL",
  "check_interval_minutes": 0.5,  // 0.5 minutes = 30 seconds
  "notifications": {
    "email": { ... },
    "desktop": { ... },
    "webhook": { ... }
  }
}
```

## How It Works

1. **Fetches the booking page** using the URL in your config (every 30 seconds)
2. **Parses available appointments** and extracts date/time information
3. **Displays all available dates** in the terminal after each check
4. **Compares with previous state** to detect any changes
5. **Sends notifications** when new appointments are detected or appointments are filled
6. **Saves the state** and waits for the next check interval
7. **Repeats** continuously

## Tips

- **Check Interval**: Currently set to 30 seconds for rapid detection. You can adjust in `config.json` if needed.
- **Run in Background**: You can run this in a Terminal window and minimize it
- **Keep It Running**: Leave it running overnight to catch early morning appointment releases
- **Multiple Notifications**: Enable multiple notification methods for redundancy
- **Watch the Terminal**: See all available dates/times displayed in real-time after each check

## Advanced Usage

### Run in Background (Terminal)

```bash
# Run in background
nohup python3 monitor.py > monitor.log 2>&1 &

# Check if it's running
ps aux | grep monitor.py

# Stop it
pkill -f monitor.py
```

### Check the Logs

The script outputs to the console. If running in background with `nohup`:

```bash
tail -f monitor.log
```

## Troubleshooting

### "Config file not found"
- Make sure you're in the `appointment-monitor` directory
- The `config.json` file should exist (it's already created for you)

### Email not sending
- Check your SMTP settings
- For Gmail, use an App Password, not your regular password
- Make sure "Less secure app access" is not required

### No notifications
- Check that at least one notification method is enabled in config.json
- For desktop notifications, make sure Terminal has notification permissions (System Settings → Notifications)

## Files

- `monitor.py` - Main monitoring script
- `config.json` - Your configuration (DO NOT commit this if using email passwords)
- `config.example.json` - Template configuration
- `state.json` - Automatically created to track previous states
- `requirements.txt` - Python dependencies

## Security Note

⚠️ **Important**: If you're using email notifications with passwords, make sure to:
- Use App Passwords (not your main email password)
- Keep `config.json` private
- Add `config.json` to `.gitignore` if committing to version control

## Support

For issues or questions, the script outputs detailed logs to help diagnose problems.

---

**Happy appointment hunting! 🎯**


RaviAlgo split-runner deployment

Fixed assignment (India time, Monday-Friday)
- 08:30: Render sends the Upstox login link to Telegram.
- After login, Render validates the callback and saves the access token to Dsheet!B1.
- 09:01-15:45: Silly runs only Upstock5.py; Render runs only option_bot.py.
- Both scripts read the token from Dsheet!B1 and update their separate spreadsheets.
- Both live Sheet refresh loops are configured for 5 seconds.
- There is no daily server-selection button because each host has a fixed, different bot.

Sheets
- Upstock5.py uses SPREADSHEET_NAME (default: Dsheet) and writes its main/Breakout data.
- option_bot.py uses OPTION_SPREADSHEET_NAME (default: option Dsheet) and reads TOKEN_SPREADSHEET_NAME (default: Dsheet).
- The two bots do not write the same spreadsheet.

Render
- Keep the start command: gunicorn --config gunicorn_config.py main:app
- Auto-deploy is enabled from GitHub main.
- Required secret environment variables: GOOGLE_SERVICE_ACCOUNT_JSON, TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID, UPSTOX_API_KEY, and UPSTOX_API_SECRET.
- UPSTOX_REDIRECT_URI must be exactly https://ravialgo.onrender.com/upstox/callback and
  registered on the same Upstox Developer App.
- option_bot.py is supervised by main.py and retried after unexpected exits.
- The legacy scheduled GitHub Actions option-bot workflow is disabled to prevent a second writer.

Silly Development
- Keep the startup command: /usr/local/bin/python /home/container/silly_app.py
- Only silly_app.py and Upstock5.py need to be replaced for this split assignment.
- Existing Google service-account credentials must remain present on Silly; never put them in Git.
- The free container has 256 MiB RAM. It now runs one bot instead of two, but its live memory
  use must be checked during market hours after deployment.

Verification
- Python syntax compilation passes for the package.
- A market-hours run is required to verify live Upstox data, Google Sheets writes, and memory.
- Do not run either bot manually while the scheduled host processes are active.


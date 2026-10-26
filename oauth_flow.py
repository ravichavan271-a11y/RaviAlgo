"""Upstox OAuth callback: exchange the returned code and store token in Dsheet!B1."""

from datetime import datetime, timezone
import hashlib
import hmac
import os
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests



AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
PROFILE_URL = "https://api.upstox.com/v2/user/profile"
STATE_MAX_AGE_SECONDS = 15 * 60


def _client_id():
    # Support both the names in the deploy package and Render's existing env names.
    return (os.environ.get("UPSTOX_CLIENT_ID", "") or os.environ.get("UPSTOX_API_KEY", "")).strip()


def _client_secret():
    return (os.environ.get("UPSTOX_CLIENT_SECRET", "") or os.environ.get("UPSTOX_API_SECRET", "")).strip()


def _redirect_uri():
    configured = os.environ.get("UPSTOX_REDIRECT_URI", "").strip()
    if configured:
        return configured
    base_url = (
        os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        or os.environ.get("SILLY_PUBLIC_URL", "").strip()
    ).rstrip("/")
    return f"{base_url}/upstox/callback" if base_url else ""


def write_access_token(access_token):
    """Save the verified token to Dsheet!B1 using the configured Google identity."""
    import json
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(env_json), scope)
        client = gspread.authorize(credentials)
    else:
        credentials = None
        for path in (
            os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
            "service_account.json",
            "./service_account.json",
            "/etc/secrets/service_account.json",
        ):
            if path and os.path.isfile(path):
                try:
                    credentials = ServiceAccountCredentials.from_json_keyfile_name(path, scope)
                    break
                except Exception:
                    continue
        if credentials is None:
            raise RuntimeError("Google service-account credentials are not configured.")
        client = gspread.authorize(credentials)
    spreadsheet = client.open(os.environ.get("TOKEN_SPREADSHEET_NAME", "Dsheet"))
    spreadsheet.sheet1.update_acell("B1", access_token)


def build_login_url():
    configured_url = os.environ.get("UPSTOX_LOGIN_URL", "").strip()
    parsed = urlsplit(configured_url or AUTH_URL)
    default_auth = urlsplit(AUTH_URL)
    if parsed.path != default_auth.path or parsed.netloc != default_auth.netloc:
        parsed = default_auth
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    client_id = _client_id() or params.get("client_id", "").strip()
    client_secret = _client_secret()
    redirect_uri = _redirect_uri() or params.get("redirect_uri", "").strip()

    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("Set UPSTOX_CLIENT_ID, UPSTOX_CLIENT_SECRET, and UPSTOX_REDIRECT_URI for automatic token saving.")

    timestamp = int(datetime.now(timezone.utc).timestamp())
    nonce = secrets.token_urlsafe(18)
    state_payload = f"{timestamp}.{nonce}"
    signature = hmac.new(client_secret.encode(), state_payload.encode(), hashlib.sha256).hexdigest()
    state = f"{state_payload}.{signature}"

    params.update({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    path = parsed.path or urlsplit(AUTH_URL).path
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or urlsplit(AUTH_URL).netloc
    return urlunsplit((scheme, netloc, path, urlencode(params), ""))


def send_login_reminder(runner_mode):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print("08:30 login reminder skipped; Telegram credentials are not configured.", flush=True)
        return False

    try:
        login_url = build_login_url()
        message = (
            "08:30 IST: Log in to Upstox using the link below. After approval, "
            "the callback will save the new token to Dsheet!B1 automatically. "
            "The trading bots start at 09:01."
        )
    except Exception as exc:
        print(f"08:30 login reminder not sent; Upstox login link is not configured ({type(exc).__name__}).", flush=True)
        return False
    payload = {"chat_id": chat_id, "text": f"{message}\nLogin: {login_url}", "link_preview_options": {"is_disabled": True}}
    try:
        response = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload, timeout=15)
        response.raise_for_status()
        print(f"08:30 Upstox login reminder sent for {runner_mode}.", flush=True)
        return True
    except requests.RequestException as exc:
        print(f"08:30 Upstox login reminder failed: {type(exc).__name__}.", flush=True)
        return False


def validate_state(state):
    client_secret = _client_secret()
    try:
        timestamp_text, nonce, signature = state.split(".", 2)
        timestamp = int(timestamp_text)
    except (AttributeError, ValueError):
        return False
    age = int(datetime.now(timezone.utc).timestamp()) - timestamp
    if not nonce or age < 0 or age > STATE_MAX_AGE_SECONDS or not client_secret:
        return False
    payload = f"{timestamp_text}.{nonce}"
    expected = hmac.new(client_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def exchange_and_store(code, state):
    if not validate_state(state):
        raise ValueError("OAuth state is invalid or expired. Request a fresh login link.")

    client_id = _client_id()
    client_secret = _client_secret()
    redirect_uri = _redirect_uri()
    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("OAuth callback configuration is incomplete.")

    response = requests.post(
        TOKEN_URL,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        errors = payload.get("errors") or []
        first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
        code_text = str(first_error.get("errorCode") or first_error.get("error_code") or "unknown")
        message_text = str(first_error.get("message") or "token exchange rejected")
        # Keep logs useful without ever printing the auth code or either app secret.
        print(
            f"Upstox token exchange rejected: HTTP {response.status_code}, "
            f"{code_text}: {message_text}.",
            flush=True,
        )
        raise RuntimeError(f"Upstox rejected token exchange ({code_text}).")
    access_token = str(payload.get("access_token", "")).strip()
    if not access_token:
        raise RuntimeError("Upstox token response did not contain an access token.")

    profile = requests.get(
        PROFILE_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=15,
    )
    profile.raise_for_status()
    write_access_token(access_token)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if bot_token and chat_id:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": "Upstox login complete: the new access token was saved to Dsheet!B1."},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Token-saved Telegram notice failed: {type(exc).__name__}.", flush=True)
    return True


def handle_callback(path):
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(path).query)
    if query.get("error"):
        return 400, "Upstox login was cancelled or rejected. Return to Telegram and request a new login link."
    code = (query.get("code") or [""])[0].strip()
    state = (query.get("state") or [""])[0].strip()
    if not code:
        return 400, "Authorization code was not returned. Start again from the Telegram login link."
    try:
        exchange_and_store(code, state)
    except SystemExit:
        return 409, "This server is not the active trading host. The token was not saved."
    except Exception as exc:
        print(f"Upstox OAuth callback failed: {type(exc).__name__}.", flush=True)
        return 400, "Login did not complete. Check server configuration, then request a fresh Telegram login link."
    return 200, "Upstox login successful. The access token was saved to Dsheet!B1. You can close this page."


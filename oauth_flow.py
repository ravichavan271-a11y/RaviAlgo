"""Upstox OAuth callback: exchange the returned code and store token in Dsheet!B1."""

from datetime import datetime, timezone
import hashlib
import hmac
import os
import secrets
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests



AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
PROFILE_URL = "https://api.upstox.com/v2/user/profile"
STATE_MAX_AGE_SECONDS = 15 * 60
IST = ZoneInfo("Asia/Kolkata")
_LOGIN_STATE_LOCK = threading.Lock()
_LOGIN_COMPLETED_DATE = None
_LOGIN_CHECKED_DATE = None
_LOGIN_CHECK_RESULT = False


def _get_sheet_client():
    import json
    import gspread
    from google.oauth2.service_account import Credentials
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        try:
            credentials = Credentials.from_service_account_info(json.loads(env_json), scopes=scope)
            return gspread.authorize(credentials)
        except Exception as exc:
            # The legacy oauth2client flow is already used by option_bot.py in
            # this deployment. Keep it as a compatibility fallback for the
            # OAuth callback if google-auth/gspread raises a runtime error.
            try:
                credentials = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(env_json), scope)
                client = gspread.authorize(credentials)
                print("Google service-account environment JSON loaded via oauth2client fallback.", flush=True)
                return client
            except Exception as fallback_exc:
                detail = getattr(exc, "name", None) if isinstance(exc, AttributeError) else None
                fallback_detail = getattr(fallback_exc, "name", None) if isinstance(fallback_exc, AttributeError) else None
                suffix = f", missing attribute: {detail}" if detail else ""
                fallback_suffix = f", missing attribute: {fallback_detail}" if fallback_detail else ""
                print(
                    "Google service-account environment JSON could not be loaded "
                    f"({type(exc).__name__}{suffix}; oauth2client fallback "
                    f"{type(fallback_exc).__name__}{fallback_suffix}).",
                    flush=True,
                )

    checked_files = []
    file_errors = []
    for path in (
        os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
        "creds.json",
        "./creds.json",
        "/etc/secrets/creds.json",
        "service_account.json",
        "./service_account.json",
        "/etc/secrets/service_account.json",
    ):
        if path and os.path.isfile(path):
            checked_files.append(os.path.basename(path))
            try:
                credentials = Credentials.from_service_account_file(path, scopes=scope)
                return gspread.authorize(credentials)
            except Exception as exc:
                first_detail = getattr(exc, "name", None) if isinstance(exc, AttributeError) else None
                try:
                    credentials = ServiceAccountCredentials.from_json_keyfile_name(path, scope)
                    client = gspread.authorize(credentials)
                    print(
                        f"Google service-account file {os.path.basename(path)} loaded via oauth2client fallback.",
                        flush=True,
                    )
                    return client
                except Exception as fallback_exc:
                    fallback_detail = getattr(fallback_exc, "name", None) if isinstance(fallback_exc, AttributeError) else None
                    detail_text = f" (missing attribute: {first_detail})" if first_detail else ""
                    fallback_text = f"; fallback missing attribute: {fallback_detail}" if fallback_detail else ""
                    file_errors.append(
                        f"{os.path.basename(path)}:{type(exc).__name__}{detail_text}/"
                        f"{type(fallback_exc).__name__}{fallback_text}"
                    )
                    continue
    if file_errors:
        summary = ", ".join(dict.fromkeys(file_errors))
        print(f"Google service-account file loading failed ({summary}). Check that the secret file is a valid service-account JSON key.", flush=True)
        raise RuntimeError(f"Google service-account files could not be loaded ({summary}).")
    if checked_files:
        raise RuntimeError("Google service-account files were found but could not be initialized.")
    print("Google service-account file not found; checked configured path, creds.json, and service_account.json.", flush=True)
    raise RuntimeError("Google service-account credentials are not configured.")


def _saved_token_is_valid():
    try:
        token = str(
            _get_sheet_client()
            .open(os.environ.get("TOKEN_SPREADSHEET_NAME", "Dsheet"))
            .sheet1.cell(1, 2).value or ""
        ).strip()
        if len(token) < 50:
            return False
        response = requests.get(
            PROFILE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10,
        )
        return response.status_code == 200
    except Exception as exc:
        print(f"Could not verify the saved Upstox token before login reminders ({type(exc).__name__}).", flush=True)
        return False


def login_completed_today():
    """Whether this Render process completed Upstox login today (India time)."""
    global _LOGIN_CHECKED_DATE, _LOGIN_CHECK_RESULT, _LOGIN_COMPLETED_DATE
    today = datetime.now(IST).date()
    with _LOGIN_STATE_LOCK:
        if _LOGIN_COMPLETED_DATE == today:
            return True
        if _LOGIN_CHECKED_DATE == today:
            return _LOGIN_CHECK_RESULT
        _LOGIN_CHECKED_DATE = today

    token_valid = _saved_token_is_valid()
    with _LOGIN_STATE_LOCK:
        _LOGIN_CHECK_RESULT = token_valid
        if token_valid:
            _LOGIN_COMPLETED_DATE = today
        return token_valid


def _mark_login_completed():
    global _LOGIN_COMPLETED_DATE, _LOGIN_CHECKED_DATE, _LOGIN_CHECK_RESULT
    with _LOGIN_STATE_LOCK:
        _LOGIN_COMPLETED_DATE = datetime.now(IST).date()
        _LOGIN_CHECKED_DATE = _LOGIN_COMPLETED_DATE
        _LOGIN_CHECK_RESULT = True


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
    client = _get_sheet_client()
    spreadsheet = client.open(os.environ.get("TOKEN_SPREADSHEET_NAME", "Dsheet"))
    worksheet = spreadsheet.get_worksheet(0)
    if worksheet is None:
        raise RuntimeError("The token spreadsheet has no first worksheet.")
    try:
        worksheet.update_cell(1, 2, access_token)
        saved_value = str(worksheet.cell(1, 2).value or "").strip()
    except Exception as exc:
        print(f"Google Sheets token write/readback failed: {type(exc).__name__}.", flush=True)
        raise RuntimeError(f"Google Sheets token write/readback failed ({type(exc).__name__}).") from exc
    if not hmac.compare_digest(saved_value, access_token):
        raise RuntimeError("Google Sheets token write could not be verified in Dsheet!B1.")
    print("Upstox access token saved and verified in Dsheet!B1.", flush=True)


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


def send_login_reminder(runner_mode, scheduled_time):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print(f"{scheduled_time} login reminder skipped; Telegram credentials are not configured.", flush=True)
        return False

    try:
        login_url = build_login_url()
        message = (
            f"{scheduled_time} IST: Log in to Upstox using the link below. After approval, "
            "the callback will save the new token to Dsheet!B1 automatically. "
            "If you have not logged in yet, another link will arrive in 15 minutes. "
            "Links expire after 15 minutes; successful login stops further reminders. "
            "The trading bots start at 09:01."
        )
    except Exception as exc:
        print(f"{scheduled_time} login reminder not sent; Upstox login link is not configured ({type(exc).__name__}).", flush=True)
        return False
    payload = {"chat_id": chat_id, "text": f"{message}\nLogin: {login_url}", "link_preview_options": {"is_disabled": True}}
    try:
        response = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload, timeout=15)
        response.raise_for_status()
        print(f"{scheduled_time} Upstox login reminder sent for {runner_mode}.", flush=True)
        return True
    except requests.RequestException as exc:
        print(f"{scheduled_time} Upstox login reminder failed: {type(exc).__name__}.", flush=True)
        return False


def validate_state(state):
    if login_completed_today():
        return False
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
        errors = payload.get("errors") or []
        first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
        code_text = str(first_error.get("errorCode") or first_error.get("error_code") or "none")
        message_text = str(first_error.get("message") or "No error message was returned")
        print(
            f"Upstox token response had no access_token: HTTP {response.status_code}, "
            f"{code_text}: {message_text}.",
            flush=True,
        )
        raise RuntimeError("Upstox token response did not contain an access token.")

    profile = requests.get(
        PROFILE_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=15,
    )
    profile.raise_for_status()
    write_access_token(access_token)
    _mark_login_completed()
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
    except ValueError:
        print("Upstox OAuth callback failed: login state is invalid or expired.", flush=True)
        return 400, "This login link has expired or is invalid. Use the latest Telegram login link."
    except RuntimeError as exc:
        # RuntimeError messages in this module contain only safe categories/codes, never secrets or tokens.
        print(f"Upstox OAuth callback failed: {type(exc).__name__}: {exc}", flush=True)
        return 400, "Upstox token approval failed or could not be saved. Check Render logs, then use the latest Telegram link."
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        print(
            f"Upstox OAuth callback request failed: {type(exc).__name__}; HTTP {status_code or 'unknown'}.",
            flush=True,
        )
        return 400, "Upstox or Google Sheets request failed. Check Render logs, then use the latest Telegram link."
    except Exception as exc:
        print(f"Upstox OAuth callback failed: {type(exc).__name__}.", flush=True)
        return 400, "Login did not complete. Check server configuration, then request a fresh Telegram login link."
    return 200, "Upstox login successful. The access token was saved to Dsheet!B1. You can close this page."


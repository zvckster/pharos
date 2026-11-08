"""Built-in delivery adapters. Credentials stay outside the catalogue and state."""

import email.message
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward notification credentials or POST bodies to a redirect.
        return None


def secret(channel, field):
    variable = channel.get(field + "_env")
    if not variable:
        return None
    value = os.environ.get(variable)
    if not value:
        raise ValueError("Notification secret environment variable is missing")
    return value


def body(event):
    if event.get("diagnostic"):
        return event["message"]
    return "\n\n".join(
        f"{change['name']}: {change.get('installed') or 'installed version unknown'} → {change['version']}\n{change['url']}"
        for change in event["changes"]
    )


def send(channel, event):
    kind = channel["type"]
    token = secret(channel, "token")
    text = body(event)
    if kind == "email":
        message = email.message.EmailMessage()
        message["Subject"] = "[Pharos] " + event["title"]
        message["From"] = channel["from"]
        message["To"] = ", ".join(channel["to"])
        message["Message-ID"] = f"<pharos-{event['id']}@localhost>"
        message.set_content(text)
        secure = channel.get("security", "starttls")
        port = channel.get("port", 465 if secure == "tls" else 587)
        if secure == "tls":
            client = smtplib.SMTP_SSL(channel["host"], port, timeout=20, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(channel["host"], port, timeout=20)
        with client:
            if secure == "starttls":
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if channel.get("username"):
                client.login(channel["username"], secret(channel, "password") or "")
            refused = client.send_message(message)
            if refused:
                raise RuntimeError("Some email recipients were refused")
        return
    url = channel["url"]
    headers = {"Content-Type": "application/json", "Idempotency-Key": event["id"]}
    if kind == "ntfy":
        # JSON publishing handles Unicode titles without invalid HTTP headers.
        payload = {
            "topic": channel.get("topic", "pharos"),
            "title": event["title"],
            "message": text,
            "tags": ["package"],
        }
        if len(text.encode()) > 3500:
            payload["message"] = (
                text.encode()[:3400].decode(errors="ignore") + "\nAdditional releases are in the Pharos report."
            )
        if token:
            headers["Authorization"] = "Bearer " + token
    elif kind == "gotify":
        url = url.rstrip("/") + "/message"
        if not token:
            raise ValueError("Gotify requires token_env")
        headers["X-Gotify-Key"] = token
        payload = {"title": event["title"], "message": text, "priority": channel.get("priority", 3)}
    else:
        if token:
            headers["Authorization"] = "Bearer " + token
        payload = {"schema_version": 1, **{k: v for k, v in event.items() if k != "delivered"}, "message": text}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError("Notification endpoint rejected delivery")
    except urllib.error.HTTPError as error:
        error.close()
        raise


def deliver(state, channels, persist):
    errors = []
    configured = {channel["id"]: channel for channel in channels}
    for event in state["pending"]:
        # Bind legacy/unrouted events on their first delivery attempt. Once bound,
        # removing a channel cannot silently discard its outstanding delivery.
        if not event.get("targets") and configured:
            event["targets"] = list(configured)
            persist(state)
        for ident in event.get("targets", []):
            if ident in event["delivered"]:
                continue
            if ident not in configured:
                errors.append(
                    {"channel": ident, "error": "Target channel is not configured; pending delivery retained"}
                )
                continue
            try:
                send(configured[ident], event)
            except Exception:
                errors.append(
                    {"channel": ident, "error": "Delivery failed; check endpoint, credentials and connectivity"}
                )
            else:
                event["delivered"].append(ident)
                persist(state)
    state["pending"] = [
        event
        for event in state["pending"]
        if not event.get("targets") or not set(event["targets"]).issubset(event["delivered"])
    ]
    persist(state)
    return errors

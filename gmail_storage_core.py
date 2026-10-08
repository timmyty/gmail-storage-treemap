"""Gmail Storage Treemap: local Gmail metadata scanner and storage analysis. Python 3.10+."""
from __future__ import annotations

import base64
import concurrent.futures
import csv
import datetime as dt
import email.policy
import hashlib
import http.server
import json
import math
import random
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from email.parser import BytesHeaderParser
from email.utils import parseaddr, parsedate_to_datetime

SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
API = "https://gmail.googleapis.com/gmail/v1/users/me/"
# Current Gmail projects allow 6,000 units/user/minute; messages.get costs 20.
# Use 4,800 units/minute to leave headroom and charge every retry too.
QUOTA_UNITS_PER_SECOND = 80.0


class Cancelled(Exception):
    pass


def readable_size(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.2f} {unit}"
        n /= 1024


def date_string(value):
    try:
        return dt.datetime.fromtimestamp(int(value) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError):
        return "Unknown"


def normal_message(message, labels):
    headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
    sender = parseaddr(headers.get("from", ""))[1].lower() or headers.get("from", "Unknown sender")
    size = message.get("sizeEstimate")
    if not isinstance(size, int) or size < 0:
        raise ValueError("Gmail did not return a valid message size estimate.")
    return {
        "id": message["id"], "threadId": message.get("threadId", ""),
        "sender": sender, "subject": headers.get("subject", "(No subject)"),
        "date": date_string(message.get("internalDate")), "size": size,
        "labels": [labels.get(s, s) for s in message.get("labelIds", [])],
        "messageId": headers.get("message-id", ""),
    }


def group_key(message, mode):
    if mode == "Sender":
        return message["sender"]
    if mode == "Domain":
        return message["sender"].rsplit("@", 1)[-1]
    if mode == "Year":
        return message["date"][:4] if message["date"] != "Unknown" else "Unknown"
    labels = message.get("labels", [])
    if mode == "Location":
        for key, title in (("TRASH", "Trash"), ("SPAM", "Spam"), ("DRAFT", "Drafts"), ("SENT", "Sent"), ("INBOX", "Inbox")):
            if key in labels:
                return title
        return "Archived / other"
    return " + ".join(sorted(labels)) or "No labels"


def groups(messages, mode):
    result = {}
    for message in messages:
        key = group_key(message, mode)
        item = result.setdefault(key, {"name": key, "size": 0, "messages": []})
        item["size"] += message["size"]
        item["messages"].append(message)
    return sorted(result.values(), key=lambda i: (-i["size"], i["name"]))


def treemap(items, x, y, width, height):
    """Balanced binary treemap. Area exactly represents bytes before UI gutters."""
    items = [i for i in items if i["size"] > 0]
    if not items or width <= 0 or height <= 0:
        return []
    if len(items) == 1:
        return [(items[0], x, y, width, height)]
    total = sum(i["size"] for i in items)
    accumulated, cut = 0, 1
    best = float("inf")
    for j in range(1, len(items)):
        accumulated += items[j - 1]["size"]
        distance = abs(total / 2 - accumulated)
        if distance < best:
            best, cut = distance, j
        if accumulated >= total / 2:
            break
    fraction = sum(i["size"] for i in items[:cut]) / total
    if width >= height:
        w = width * fraction
        return treemap(items[:cut], x, y, w, height) + treemap(items[cut:], x + w, y, width - w, height)
    h = height * fraction
    return treemap(items[:cut], x, y, width, h) + treemap(items[cut:], x, y + h, width, height - h)


def request_json(url, data=None, headers=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Never put the request URL, token or client secret in error reports.
        reasons, retry_after = [], None
        try:
            detail = json.loads(exc.read())
            err = detail.get("error", {})
            description = err.get("message", "") if isinstance(err, dict) else str(err)
            if isinstance(err, dict):
                reasons = [str(e.get("reason", "")) for e in err.get("errors", []) if isinstance(e, dict)]
                reasons.append(str(err.get("status", "")))
                for item in err.get("details", []):
                    if isinstance(item, dict):
                        reasons.append(str(item.get("reason", "")))
                        delay = str(item.get("retryDelay", ""))
                        if delay.endswith("s"):
                            try:
                                retry_after = float(delay[:-1])
                            except ValueError:
                                pass
        except Exception:
            description = "Request failed"
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                try:
                    retry_after = parsedate_to_datetime(header).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    pass
        raise ApiError(exc.code, description, reasons, retry_after) from None


class ApiError(Exception):
    def __init__(self, code, description, reasons=(), retry_after=None):
        self.code = code
        self.description = description
        self.reasons = tuple(reasons)
        self.retry_after = max(0, retry_after) if retry_after is not None and math.isfinite(retry_after) else None
        super().__init__(f"Google API {code}: {description}")

    @property
    def rate_limited(self):
        text = (self.description + " " + " ".join(self.reasons)).lower()
        if any(word in text for word in ("daily", "per day", "billing")):
            return False
        return self.code == 429 or (self.code == 403 and any(word in text for word in (
            "ratelimit", "rate limit", "rate_limit", "quota exceeded", "quotaexceeded", "quota_exceeded", "resource_exhausted")))


def quota_cost(endpoint):
    if endpoint in ("profile", "labels"):
        return 1
    return 5 if endpoint == "messages" else 20


class RequestPacer:
    """One gate for all workers: waiting requests recheck any new cooldown."""
    def __init__(self, stop, progress, clock=None):
        self.stop, self.progress = stop, progress
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.next_request = self.cooldown_until = self.last_notice = 0.0
        self.units_per_second = QUOTA_UNITS_PER_SECOND

    def acquire(self, cost):
        while not self.stop.is_set():
            notice = None
            with self.lock:
                now = self.clock()
                delay = max(self.next_request, self.cooldown_until) - now
                if delay <= 0:
                    self.next_request = now + cost / self.units_per_second
                    return
                if self.cooldown_until > now and now >= self.last_notice:
                    notice = f"Gmail quota pause — retrying in {math.ceil(self.cooldown_until - now)} seconds. Results are kept."
                    self.last_notice = now + 5
            if notice:
                self.progress(notice)
            if self.stop.wait(min(delay, 1.0)):
                break
        raise Cancelled()

    def cooldown(self, seconds):
        with self.lock:
            now = self.clock()
            # Several already-running requests can fail together. Slow once per
            # quota episode, not once per worker, and never reserve future slots.
            if now >= self.cooldown_until:
                self.units_per_second = max(5.0, self.units_per_second / 2)
            self.cooldown_until = max(self.cooldown_until, now + seconds)
            self.last_notice = now + 5
        self.progress(f"Gmail quota pause — retrying in {math.ceil(seconds)} seconds at a slower rate. Results are kept.")


def authorize(client_file, stop, progress):
    with open(client_file, encoding="utf-8-sig") as stream:
        client = json.load(stream).get("installed", {})
    if not client.get("client_id"):
        raise ValueError("Choose a Google OAuth client JSON created with application type Desktop app.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    result = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/oauth/callback" or not secrets.compare_digest(params.get("state", [""])[0], state):
                self.send_error(400, "Invalid authorization response")
                return
            result.update({k: v[0] for k, v in params.items()})
            text = b"Sign-in response received. Return to Gmail Storage Treemap; you can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)

        def log_message(self, *_):
            pass

    with http.server.HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 0.5
        redirect = f"http://127.0.0.1:{server.server_port}/oauth/callback"
        params = dict(client_id=client["client_id"], redirect_uri=redirect,
                      response_type="code", scope=SCOPE, state=state,
                      code_challenge=challenge, code_challenge_method="S256",
                      access_type="offline", prompt="consent")
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
        progress("Complete Google sign-in in your browser. Waiting for permission…")
        if not webbrowser.open(url):
            raise RuntimeError("Could not open your browser. Set a default browser and try again.")
        until = time.monotonic() + 300
        while not result:
            if stop.is_set():
                raise Cancelled()
            if time.monotonic() > until:
                raise RuntimeError("Sign-in timed out. Click Connect Gmail to try again.")
            server.handle_request()
    if "error" in result:
        raise RuntimeError("Google permission was declined. Nothing was scanned.")
    token = request_json("https://oauth2.googleapis.com/token", dict(
        code=result.get("code", ""), client_id=client["client_id"],
        client_secret=client.get("client_secret", ""), redirect_uri=redirect,
        grant_type="authorization_code", code_verifier=verifier))
    return GmailClient(client, token, stop, progress)


class GmailClient:
    def __init__(self, client, token, stop, progress=lambda _: None):
        self.client, self.token, self.stop = client, token, stop
        self.expires = time.monotonic() + token.get("expires_in", 3600) - 60
        self.lock = threading.Lock()
        self.progress = progress
        self.pacer = RequestPacer(stop, progress)

    def get(self, endpoint, params=None):
        for attempt in range(8):
            if self.stop.is_set():
                raise Cancelled()
            self.pacer.acquire(quota_cost(endpoint))
            with self.lock:
                if time.monotonic() >= self.expires:
                    if not self.token.get("refresh_token"):
                        raise RuntimeError("Session expired. Reconnect and scan again.")
                    new = request_json("https://oauth2.googleapis.com/token", dict(
                        client_id=self.client["client_id"], client_secret=self.client.get("client_secret", ""),
                        refresh_token=self.token["refresh_token"], grant_type="refresh_token"))
                    self.token.update(new)
                    self.expires = time.monotonic() + new.get("expires_in", 3600) - 60
                token = self.token["access_token"]
            if self.stop.is_set():
                raise Cancelled()
            try:
                return request_json(API + endpoint + ("?" + urllib.parse.urlencode(params, doseq=True) if params else ""),
                                    headers={"Authorization": "Bearer " + token})
            except ApiError as exc:
                if exc.rate_limited:
                    if attempt == 7 or (exc.retry_after or 0) > 600:
                        raise RuntimeError("Gmail's quota is still unavailable. Collected results are preserved. Save a snapshot and use Resume scan later.") from None
                    self.pacer.cooldown(max(65.0, exc.retry_after or 0, min(2 ** attempt, 120)) + random.random())
                    continue
                if exc.code not in (500, 502, 503, 504) or attempt >= 5:
                    raise
                self.progress("Gmail temporarily unavailable — retrying the current request…")
                if self.stop.wait(2 ** attempt + random.random()):
                    raise Cancelled()


def scan_gmail(client, stop, progress, partial, resume=None, scan_progress=None):
    notify = scan_progress or (lambda _: None)
    profile = client.get("profile")
    if resume is not None:
        validate_snapshot(resume)
        if (resume.get("source") != "Gmail size estimates" or not resume.get("account") or
                resume["account"].casefold() != profile.get("emailAddress", "").casefold()):
            raise ValueError("This snapshot belongs to a different mailbox or is not a Gmail scan. Connect to the same account to resume.")
    label_map = {l["id"]: l["name"] for l in client.get("labels").get("labels", [])}
    ids, seen, page = [], set(), None
    while True:
        params = {"maxResults": 500, "includeSpamTrash": "true"}
        if page:
            params["pageToken"] = page
        result = client.get("messages", params)
        for item in result.get("messages", []):
            if item["id"] not in seen:
                seen.add(item["id"])
                ids.append(item["id"])
        progress(f"Listing messages… {len(ids):,} found (including Spam and Trash)")
        notify({"phase": "listing", "listed": len(ids), "title": "Listing messages, including Spam and Trash"})
        page = result.get("nextPageToken")
        if not page:
            break
    snapshot = {"version": 1, "source": "Gmail size estimates", "account": profile.get("emailAddress", ""),
                "scannedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "complete": False, "expected": len(ids), "failed": 0, "messages": []}
    if resume:
        snapshot["messages"] = [dict(m) for m in resume["messages"] if m["id"] in seen]
        snapshot["resumedFrom"] = resume.get("scannedAt", "earlier snapshot")
    reused = {m["id"] for m in snapshot["messages"]}
    remaining_ids = [i for i in ids if i not in reused]
    total_bytes = sum(m["size"] for m in snapshot["messages"])
    def update_progress():
        done = len(snapshot["messages"]) + snapshot["failed"]
        notify({"phase": "scanning", "title": "Reading message sizes and headers", "completed": done,
                "total": len(ids), "bytes": total_bytes, "failed": snapshot["failed"], "reused": len(reused)})
    update_progress()
    partial(snapshot)
    def read_message(message_id):
        return normal_message(client.get("messages/" + urllib.parse.quote(message_id, safe=""), {
            "format": "metadata", "metadataHeaders": ["From", "Subject", "Date", "Message-ID"],
            "fields": "id,threadId,labelIds,internalDate,sizeEstimate,payload/headers"}), label_map)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        # Bounded batches avoid allocating a Future per email in huge mailboxes.
        for start in range(0, len(remaining_ids), 60):
            if stop.is_set():
                break
            futures = [pool.submit(read_message, i) for i in remaining_ids[start:start + 60]]
            fatal = None
            for future in concurrent.futures.as_completed(futures):
                try:
                    message = future.result()
                    snapshot["messages"].append(message)
                    total_bytes += message["size"]
                except Cancelled:
                    pass
                except ApiError as exc:
                    if exc.code != 404:
                        stop.set()
                        fatal = fatal or exc
                    else:
                        snapshot["failed"] += 1
                except ValueError:
                    snapshot["failed"] += 1
                except Exception as exc:
                    stop.set()
                    fatal = fatal or exc
                update_progress()
            progress(f"Scanned {len(snapshot['messages']):,} / {len(ids):,} messages · {snapshot['failed']:,} unavailable")
            partial(snapshot)
            if fatal:
                raise fatal
    snapshot["complete"] = not stop.is_set() and snapshot["failed"] == 0 and len(snapshot["messages"]) == len(ids)
    return snapshot


def import_mbox(path, stop, progress):
    """Stream message bytes, retaining bounded headers only, never bodies."""
    messages, count, header, in_headers, started = [], 0, bytearray(), True, False
    def finish():
        if not started:
            return
        msg = BytesHeaderParser(policy=email.policy.default).parsebytes(bytes(header))
        sender = parseaddr(str(msg.get("From", "")))[1].lower() or "Unknown sender"
        try:
            date = parsedate_to_datetime(str(msg.get("Date", ""))).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OverflowError):
            date = "Unknown"
        labels = next(csv.reader([str(msg.get("X-Gmail-Labels", ""))]), [])
        system = {"Inbox": "INBOX", "Sent": "SENT", "Trash": "TRASH", "Spam": "SPAM", "Draft": "DRAFT", "Drafts": "DRAFT"}
        messages.append({"id": f"mbox-{len(messages)}", "sender": sender,
                         "subject": str(msg.get("Subject", "(No subject)")),
                         "date": date, "size": count, "labels": [system.get(x.strip(), x.strip()) for x in labels if x.strip()],
                         "messageId": str(msg.get("Message-ID", "")), "threadId": ""})
    import os
    total, consumed = os.path.getsize(path), 0
    with open(path, "rb") as stream:
        # readline(size) bounds memory even for unusually long body lines.
        line_start = True
        while True:
            line = stream.readline(1024 * 1024)
            if not line:
                break
            consumed += len(line)
            separator = line_start and line.startswith(b"From ")
            line_start = line.endswith(b"\n")
            if separator:
                finish()
                started, count, header, in_headers = True, 0, bytearray(), True
                if len(messages) % 500 == 0:
                    progress(f"Reading export… {len(messages):,} messages · {consumed / max(total, 1):.0%}")
            elif started:
                count += len(line)
                if in_headers:
                    if len(header) + len(line) > 262144:
                        raise ValueError("An email header exceeds 256 KiB; this export cannot be read safely.")
                    header.extend(line)
                    if line in (b"\n", b"\r\n"):
                        in_headers = False
            elif line.strip():
                raise ValueError("This is not an MBOX file. Extract the .mbox file from your Takeout ZIP first.")
            if stop.is_set():
                break
        if not stop.is_set():
            finish()
    return {"version": 1, "source": "MBOX exported message bytes", "account": "",
            "scannedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "complete": not stop.is_set(), "failed": 0, "messages": messages}


def validate_snapshot(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("messages"), list):
        raise ValueError("Choose a Gmail Storage Treemap snapshot JSON file.")
    seen = set()
    for message in data["messages"]:
        if not isinstance(message, dict):
            raise ValueError("Invalid snapshot message.")
        for field in ("id", "sender", "subject", "date"):
            if not isinstance(message.get(field), str):
                raise ValueError("Snapshot is missing message metadata.")
        if message["id"] in seen:
            raise ValueError("Duplicate message IDs would double-count storage.")
        seen.add(message["id"])
        if type(message.get("size")) is not int or not 0 <= message["size"] <= 10 ** 12:
            raise ValueError("Invalid message size in snapshot.")
        if not isinstance(message.get("labels", []), list) or any(not isinstance(l, str) for l in message.get("labels", [])):
            raise ValueError("Invalid labels in snapshot.")
    return data


def demo_snapshot():
    rng = random.Random(12)
    rows = []
    for sender, number, scale in [("photos@example.com", 135, 5_000_000), ("projects@example.org", 210, 1_900_000),
                                  ("receipts@example.net", 800, 55_000), ("news@example.com", 450, 110_000),
                                  ("family@example.org", 180, 2_000_000), ("alerts@example.net", 650, 16_000),
                                  ("documents@example.com", 70, 2_400_000), ("events@example.org", 200, 80_000)]:
        for i in range(number):
            rows.append({"id": f"demo-{len(rows)}", "sender": sender,
                         "subject": f"Sample {sender.split('@')[0]} message {i + 1}",
                         "date": f"{rng.randint(2016, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                         "size": int(scale * rng.uniform(0.1, 2.1)),
                         "labels": rng.choice([["INBOX"], ["SENT"], [], ["TRASH"], ["Work", "INBOX"]]),
                         "messageId": "", "threadId": ""})
    return {"version": 1, "source": "DEMO — fictional mail", "account": "", "complete": True, "messages": rows}

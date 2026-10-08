# Gmail Storage Treemap

A local Gmail storage explorer inspired by WizTree. See which senders and messages occupy the most space in an interactive treemap.

Gmail Storage Treemap is a Python/Tkinter desktop app built for Windows. It uses the Python standard library only, with no pip dependencies, hosted backend, or telemetry.

## Features

- Tiles sized by Gmail's message size estimates, including attachments.
- Group by sender, domain, year, location, or complete label set.
- Drill into groups and inspect a list sorted by message size.
- Search sender, subject, or labels, and filter by minimum size.
- A progress dialog with scan phase, counts, percentage, elapsed time, and quota countdowns.
- Stop a scan, save its partial results, and resume later.
- Open selected messages in Gmail for manual review.
- Save local metadata snapshots or export the current view as CSV.
- An offline fallback for a single Google Takeout MBOX export.
- A demo containing fictional messages so you can explore without connecting an account.

## Run

Install **Python 3.10 or later with Tkinter** from [python.org](https://www.python.org/downloads/). On Windows, the normal Python installer includes Tkinter.

Download and extract the [latest release ZIP](https://github.com/timmyty/gmail-storage-treemap/releases/latest) or clone this repository. Keep the project files together, then double-click **Start-Gmail-Storage-Treemap.cmd** on Windows, or run:

```sh
python gmail_storage_treemap.py
```

To start with fictional demo data:

```sh
python gmail_storage_treemap.py --demo
```

The source may also run on other platforms with Tkinter installed, but the desktop interface has been exercised on Windows. No standalone executable or installer is included.

## Connect your Gmail account

Each user supplies their own Google OAuth Desktop app client. **No Google credentials are distributed with this project.**

1. Create a personal project in [Google Cloud Console](https://console.cloud.google.com/projectcreate).
2. Enable the [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
3. Configure Google Auth Platform. For a personal Gmail account, choose an External audience, keep the app in Testing, and add your Gmail address as a test user.
4. Add this scope under Data Access:

   ```text
   https://www.googleapis.com/auth/gmail.metadata
   ```

5. Create an OAuth client with application type **Desktop app**, then download its JSON file.
6. Click **Connect Gmail…** in Gmail Storage Treemap, select that file, and complete Google's browser sign-in and permission review.

Open [Setup.html](Setup.html) in your browser for detailed instructions and troubleshooting. Google Workspace organizations may restrict app access. This project does not bypass those restrictions or Google's app verification requirements.

## Privacy and permissions

The Gmail connection requests the `gmail.metadata` scope. It reads message headers, labels, IDs, dates, and size estimates. It does **not** download message bodies or attachment contents, send email, change labels, or delete messages.

- OAuth sign-in uses the system browser, PKCE, a state check, and a temporary callback bound to `127.0.0.1`.
- Access and refresh tokens remain in process memory and are not written into snapshots or files by Gmail Storage Treemap.
- Gmail metadata is fetched directly from Google and processed locally.
- Snapshots and CSV exports are saved only when you choose to save them. They can include your email address, senders, subjects, and message identifiers, so keep them private.
- The optional MBOX importer reads your local export and retains message metadata and byte counts, not message bodies.
- Opening a message in Gmail sends its identifier to Gmail through your browser.

Do not commit OAuth client files, mailbox snapshots, exports, or diagnostic screenshots containing private mail. The repository's `.gitignore` excludes common credential and mailbox file formats. Demo and test data use fictional addresses.

## Storage totals and attachments

Tile areas represent each message's `sizeEstimate`, which includes its encoded content and attachments. Attachments are included in whole-message totals; individual attachments are not separately enumerated or mapped.

These estimates can differ from Google's storage quota accounting. MBOX imports measure exported message bytes instead of server size estimates. A mailbox can change during a scan.

Each message is counted once. **Label set** groups by the complete combination of labels. **Location** assigns each message using this precedence: Trash, Spam, Drafts, Sent, Inbox, then Archived / other.

The treemap shows up to 160 groups or messages at once. An **Other** tile includes all remaining bytes and can be opened to explore further. Zero-byte messages remain accessible in the list.

## Scan speed and quota handling

Gmail Storage Treemap budgets 4,800 quota units per minute. Under the Gmail API's quota schedule checked in October 2026, `messages.get` costs 20 units, allowing about four message reads per second before network overhead. Large mailboxes can take hours. Listing, metadata reads, and retries share one request limiter.

Per-minute quota errors pause all scan workers for at least 65 seconds, or Google's requested delay, and reduce the request rate before retrying. Repeated failures eventually stop the scan with its collected results marked partial. Permission failures are reported separately from temporary quotas. Run one scan at a time.

To continue an interrupted scan, **Save snapshot** before closing, reopen the snapshot, and choose **Resume scan…**. Resume verifies the mailbox account, lists current message IDs, removes records no longer present, and fetches only missing messages. Reused entries retain their previously scanned labels and sizes. Choose **Connect Gmail…** for a fully fresh scan.

## Development and tests

From the repository root:

```sh
python -m unittest discover -s tests -p "test_*.py" -v
python tests/gui_smoke.py
```

The GUI smoke test requires a graphical desktop with Tkinter. Tests use fictional mail and mocked API responses; they never access a real Gmail account. The tests cover size accounting, duplicate prevention, treemap geometry, pagination, quota errors, retry pacing, cancellation, progress reporting, and resume behavior. Passing these tests does not confirm a particular Google account's live OAuth configuration.

Build a distributable source ZIP with:

```sh
python tools/build_release.py
```

The build script packages an explicit list of source, documentation, and test files. It never includes credentials or local mailbox data.

## References

- [Gmail message resource and sizeEstimate](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages)
- [Gmail authorization scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Gmail API quotas](https://developers.google.com/workspace/gmail/api/reference/quota)
- [Google OAuth for Desktop apps](https://developers.google.com/identity/protocols/oauth2/native-app)

Gmail Storage Treemap is an independent project and is not affiliated with Google or WizTree.

## License

[MIT](LICENSE).

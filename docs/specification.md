Build a Linux-first Pushbullet application targeting Kubuntu 26.04, KDE Plasma 6 on Wayland, and Python 3.14.

The application has one standalone backend and two interfaces:

Native desktop: A PyQt6 application that starts or connects to the private backend automatically.

Optional web interface: Server-rendered HTML pages with locally bundled Bootstrap and small JavaScript modules. Web hosting is disabled by default and can be enabled through the desktop application or command line.

Both interfaces must use the same persisted data, supported capabilities, AI services, automation rules, authorization decisions, and message-sending pipeline.

Use pbdesk as a working package name. Branding can change independently.

Develop incrementally. This specification defines architectural boundaries and intended behavior, not a requirement to build every feature immediately. Prefer a functioning application over speculative abstractions.
Use Python virtual environments and pip. Verify dependencies against standard CPython 3.14 and record reproducible versions after compatibility testing.

Use FastAPI, Uvicorn, and Pydantic v2 for the backend; SQLite with SQLAlchemy and Alembic for persistence; and the official Python MCP SDK for MCP functionality.

Use PyQt6 with Qt Widgets for the native interface.

Use Jinja2, Bootstrap, ordinary CSS, and browser-native JavaScript for the web interface.

Do not introduce React, Vue, Angular, Next.js, TypeScript, JSX, npm, npx, Vite, Webpack, or a required Node.js runtime. Do not require Sass compilation. This restriction applies to the required development and testing workflow as well as production.

Do not replace the native interface with an embedded browser. Do not add Redis, Celery, Docker, or a separate database server as baseline requirements.
Implement these components in one repository:
Make port numbers configurable.

The core must not depend on Qt, HTML templates, or a running desktop window. It must be usable independently.

Only the core owns the database, Pushbullet connection, automation execution, and outbound-message pipeline.

Neither frontend nor the MCP adapter may open SQLite directly or independently call Pushbullet. Opening additional windows, browser tabs, or MCP sessions must not create additional Pushbullet listeners.

Use one core process and one Uvicorn worker initially. Implement a single-instance guard to prevent duplicate ingestion and automation.

Expose a versioned internal API, capability information, health/readiness checks, and an authenticated event feed. Treat the API as a stable contract rather than an incidental implementation detail.

The web host is a thin presentation and authentication gateway—not a second implementation of the application.
Launching the desktop application must start the core automatically when it is not already running. The user must not have to open a terminal and launch an API server first.

The launcher should check for the expected authenticated core instance, start it through the configured lifecycle manager when necessary, wait for readiness with a bounded timeout, and then connect.

Use systemd --user as the preferred lifecycle manager on the target system. Provide a documented foreground mode for development and headless use. Both modes must respect the same instance guard.

Never assume that an arbitrary process listening on the configured port is the correct backend. Detect incompatible instances and port conflicts explicitly.

Keep the GUI responsive during startup and display actionable errors.
Provide an Enable web interface setting, disabled by default.

Enabling it starts the web host. Disabling it stops the web listener and active browser event streams without stopping the core or desktop application.

Show hosting status, bind address, port, and an Open web interface action in the desktop settings.

Enabling web hosting does not automatically enable LAN access or public internet access. Bind to loopback initially.
Distinguish clearly between hiding the window, exiting the desktop interface, disabling web hosting, and stopping the entire application.

Closing the main window may hide it to the tray. Exiting the desktop interface must not unexpectedly terminate an intentionally running web host or headless automation service.

Provide an explicit stop-all action. Show the consequences before stopping services used by other clients.

Starting automatically at user login is a separate opt-in preference.
Build the web interface as a conventional server-rendered application.

Use Jinja2 templates for complete pages, reusable components, and optional HTML fragments. The web host retrieves data through the core API and renders it for the browser.

Use ordinary navigation and HTML forms for baseline functionality. Apply the Post/Redirect/Get pattern after successful form submissions.

JavaScript should enhance the experience rather than recreate an entire client-side application framework.

Suitable JavaScript responsibilities include incremental inbox updates, compose interactions, confirmation dialogs, connection status, draft autosaving, and preserving selected items.

Avoid a custom client-side router, global state-management framework, or parallel browser implementation of business rules.
The browser communicates only with the web host on its own origin.

The web host translates explicitly supported browser actions into authenticated core requests. It must not expose a generic proxy to arbitrary backend routes.

The browser must never receive the core service credential, Pushbullet access token, or AI-provider keys. It must not connect directly to port 8765.
Use Server-Sent Events through the web host for server-to-browser updates, with ordinary HTTP requests for user actions. Browsers expose SSE through EventSource, including reconnection behavior. [MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)

Start with small events indicating which records changed. Refresh the relevant HTML fragment or fetch updated data instead of replacing the whole page.

Include sequence identifiers and resynchronize after missed events or backend restarts. Bound queues and disconnect persistently slow clients rather than accumulating unlimited events.

Do not overwrite a reply being typed when an inbox update arrives. Use draft revisions or equivalent conflict detection when the same draft is open in both interfaces.

If live updates fail, retain usable navigation and manual refresh.
The released application must not require a CDN to render or operate its interfaces.

Download approved compiled Bootstrap assets during development or a deliberate dependency-update operation. Commit or package them with the application.

Use:
```
bootstrap.min.css
bootstrap.bundle.min.js
```
The bundled JavaScript includes Popper, avoiding a separate runtime download. [Bootstrap](https://getbootstrap.com/docs/5.3/getting-started/contents/)

Serve those files from the web host’s static directory. For example:
```
<link rel="stylesheet"
      href="{{ url_for('static', path='vendor/bootstrap/bootstrap.min.css') }}">

<link rel="stylesheet"
      href="{{ url_for('static', path='css/app.css') }}">

<script defer
        src="{{ url_for('static', path='vendor/bootstrap/bootstrap.bundle.min.js') }}">
</script>

<script type="module"
        src="{{ url_for('static', path='js/app.js') }}">
</script>
```
Use system fonts initially. Bundle any additional fonts, icons, or libraries locally with their licenses.

Record each vendored dependency’s version, source, license, and checksum in a manifest. Dependency updates must be intentional and reviewable—not automatic downloads at startup.

Inspect CSS imports, font URLs, JavaScript imports, and source-map references for unintended remote dependencies.

Keep application-authored JavaScript readable and modular. Do not add a build system merely to minify a few small files.

Include templates and static assets in the Python distribution and test their availability after installing the built package outside the repository.

Local assets do not mean Pushbullet works without internet access. The requirement is that the interface and retained local history remain usable when external services or CDNs are unavailable.
Maintain a small feature-parity matrix covering the desktop and web interfaces.

The intended shared workflows are inbox browsing, search, filtering, item details, supported message composition, drafts, AI assistance, automation controls, device status, and activity history.

Implement each operation in the core first, then expose it through the appropriate interface.

The web interface should adapt to phones and desktop browsers. Use a list-and-detail layout on wide screens and a straightforward navigation flow on narrow screens.

The native application should use appropriate Qt models, views, keyboard shortcuts, and dialogs. Keep networking and expensive work off the GUI thread.

KDE-only functionality—such as the system tray and desktop notification integration—does not require a browser imitation. Security-sensitive local operations, including service installation and credential-store configuration, may remain local-only.

Record intentional differences rather than silently allowing the interfaces to diverge.

Only one application component should emit KDE notifications, regardless of how many interfaces are open. Keep that adapter optional so the core can also run headlessly.
Use SQLite as the single authoritative local store. Apply schema migrations from the beginning.

Configure foreign keys, bounded transactions, a busy timeout, and an appropriate journal mode. Verify FTS5 availability before enabling full-text search.

Persist source identifiers, device identity, UTC timestamps, synchronization progress, drafts, action states, and audit records. Do not infer conversation identity solely from display names.

Use retention settings that cover primary records, search indexes, and stored attachments. Provide a consistent backup-and-restore procedure.

Store provider secrets through a verified secure credential backend. Never silently fall back to plaintext storage when KWallet or Secret Service is unavailable.
Use only officially documented APIs. Maintain a capability document linking implemented features to their documentation and verification status.

The public documentation describes mirrored notifications and SMS creation, but those must not be treated as proof of a complete incoming-SMS history interface. A notification preview is not automatically an addressable SMS conversation. [docs.pushbullet.com](https://docs.pushbullet.com/)

Only enable replies when a trustworthy destination and sending device are available or explicitly supplied by the user. Do not infer telephone numbers from notification titles.

Handle reconnects, duplicate events, notification updates, rate limits, and malformed payloads. Keep stored pushes distinct from ephemeral events; do not promise recovery of notifications that were never received. [docs.pushbullet.com](https://docs.pushbullet.com/)

Implement documented encryption support through maintained cryptographic libraries. An unavailable key should produce a clear locked state, not silent data loss.

Use a persistent outbox. Distinguish pending approval, ready to submit, upstream accepted, failed, and uncertain outcomes. Never blindly retry a potentially completed send after an ambiguous failure.
Start with a single-owner application and no public registration.

Bootstrap the web administrator through the local desktop interface or a local command. Do not expose an unauthenticated administrator-creation page.

Store account records, password hashes, sessions, and revocation state in SQLite through the core. Use a maintained Argon2id implementation for password hashing.

Use opaque server-side browser sessions. Store session-token hashes rather than plaintext session tokens.

Apply session expiration, rotation, revocation, login throttling, and appropriate HttpOnly, SameSite, and HTTPS Secure cookie settings. Keep any loopback HTTP exception explicit and separate from remote deployment. [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)

The web host authenticates to the core with a scoped service credential. User requests must additionally carry a verifiable user session. A forwarded username or user ID is not sufficient authorization.

Protect state-changing forms and JavaScript requests against CSRF. Do not perform state changes through GET requests. Validate relevant origins and use explicit host allowlists. [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html?utm_source=chatgpt.com)

Enable HTML autoescaping in Jinja2. Never mark received notification bodies or AI output as trusted HTML. When updating text through JavaScript, use safe text insertion rather than interpolating untrusted content into markup. Jinja provides configurable HTML autoescaping. [Jinja Documentation](https://jinja.palletsprojects.com/en/stable/api/)

Define and test a Content Security Policy appropriate to the bundled assets. Do not solve component compatibility problems by broadly permitting arbitrary scripts.

Do not automatically load remote images or previews from messages. Avoid persistent browser storage of message contents and credentials by default.

Require authentication for the private core even on loopback. Use distinct, revocable credentials for the desktop, web host, and MCP adapter.
Keep AI optional and disabled until configured. Ordinary inbox and messaging operations must continue when an AI provider is unavailable.

Implement one provider end to end before adding several adapters.

Support classification, summaries, and editable reply drafts. Keep outputs associated with their source records so users can inspect what the model used.

Cloud processing must be opt-in, with controls over eligible applications, devices, conversations, and sensitive content. Keep secrets and unrelated history out of model requests.

Set limits on context size, concurrency, runtime, and usage. Distinguish measured usage from estimated cost.

Treat received content as untrusted data. A message requesting that the AI change settings, disclose secrets, or send another message must not acquire those permissions.
All outgoing actions—from either interface, automation, or MCP—must use the same authorization and outbox pipeline.

Require approval by default. Enable automatic sending only through explicit rules with known recipients, supported channels, selected devices, cooldowns, and message limits.

The model must not independently select a new recipient or expand its permissions.

Bind approvals to the exact recipient, device, and message contents. Editing an approved action invalidates its approval.

Include dry-run mode, loop prevention, deduplication, action expiry, audit history, and a global automation pause control available in both interfaces.
Use the official Python MCP SDK, pinned to a compatible version. Start with local stdio; reserve Streamable HTTP for a later, separately secured remote feature. These are supported transports in the official SDK. [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/run/)

Expose narrow tools for capabilities, devices, notification search, item retrieval, reply drafting, action requests, and action status.

Sending tools must enter the normal approval pipeline. An MCP client must not approve its own action simply by passing confirmed=true.

Do not expose arbitrary SQL, shell commands, unrestricted HTTP requests, or secret retrieval.

Test through the official Python client and automated protocol tests. Do not make a Node-based inspector a required development dependency.
Keep remote hosting separate from simply enabling the local web interface.

Design the web host to work behind an externally managed reverse tunnel or HTTPS reverse proxy. Do not build a tunneling system into the application.

Only the web-host port may be exposed. The private core must remain bound to loopback.

Before remote deployment, verify secure sessions, origin and host checks, proxy trust configuration, rate limiting, access revocation, and a second authentication factor.

A tunnel must not bypass the application’s login system. Trust forwarded headers only from explicitly configured proxies.

Keep remote MCP disabled until its own authorization requirements are implemented. Browser login alone must not be assumed to provide MCP authorization.

Show connectivity status accurately. Do not present remote access as available when the computer or required services are offline.
Keep web templates and assets inside the Python package rather than creating a separate frontend project:
```
pyproject.toml
src/
  pbdesk/
    domain/
    storage/
    services/
    pushbullet/
    api/
    desktop/
    ai/
    automation/
    mcp_server/
    web/
      routes/
      templates/
        base.html
        login.html
        inbox.html
        details.html
        settings.html
        partials/
      static/
        vendor/
          bootstrap/
        css/
          app.css
        js/
          app.js
          inbox.js
          compose.js
      vendor-manifest.json
tests/
docs/
packaging/
```
Create modules when they have actual responsibilities. Do not scaffold unused plugin frameworks.

Separate optional dependency groups so the core can be installed without Qt and the desktop can run without enabling web hosting.

Provide entry points for desktop launch, foreground core execution, optional web hosting, MCP, local administrator setup, and diagnostics.

Diagnostics should report dependency versions, service status, database capabilities, credential-backend availability, and connection state without exposing credentials or message contents.
Use pytest for core and integration tests, pytest-qt for the desktop, and Python-based Selenium tests for browser workflows. Selenium supplies pip-installable Python bindings and browser-driver integration. [Selenium](https://www.selenium.dev/selenium/docs/api/py/?utm_source=chatgpt.com)

Use sanitized fixtures and fake providers. Automated tests must not send real messages or require paid AI access.

Test the application with Node.js absent. Test the web interface with external asset requests blocked. Inspect browser network activity to confirm that styles, scripts, fonts, and icons come from the local web host.

Verify startup races, repeated desktop launches, core restarts, web-host enable/disable behavior, authentication expiry, CSRF protection, duplicate events, draft conflicts, and uncertain send outcomes.

Perform real Plasma Wayland checks separately from headless GUI tests. Report what was actually verified.
Inspect the repository and environment, then complete compatibility checks and the smallest useful desktop slice.

Demonstrate a supported event being received, persisted once, displayed in the native inbox, and producing one KDE notification. Demonstrate that launching the desktop starts the core automatically and that restarting the core does not duplicate the event.

Include a minimal, disabled-by-default web-host smoke test proving that a Jinja2 page and bundled Bootstrap assets work without Node.js or CDN access. Do not build the entire web interface before the native foundation works.

Finish with runnable commands, tests performed, known limitations, and the next concrete milestone.

The resulting product is one Python application with a private core, a native PyQt interface, and an optional locally served HTML interface—not a desktop application plus a separate JavaScript application.
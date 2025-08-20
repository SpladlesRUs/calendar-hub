# calendar-hub

A simple FastAPI application for aggregating and embedding calendars.

## Admin login

Set the `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables. To obtain an admin token, send a `POST` request to `/admin/login` with form fields `username` and `password`. The endpoint returns JSON containing a `token` value.

Include this token in subsequent admin requests either as a query parameter `?token=YOUR_TOKEN` or as an `X-Admin-Token` header. Visiting the site root `/` shows a login form that will authenticate and redirect to the dashboard at `/admin`.

## Embedding calendars

Calendars can be embedded on other sites in two ways:

### Iframe

```html
<iframe src="https://your-domain/c/<slug>/embed" style="border:0;width:100%;height:600px"></iframe>
```

### JavaScript loader

Include FullCalendar and the iCalendar plugin on the host page:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.css">
<link rel="stylesheet" href="https://your-domain/static/styles.css">
<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@fullcalendar/icalendar@6.1.15/index.global.min.js"></script>
```

Then add a container and load the calendar-specific script:

```html
<div id="calendar-container"></div>
<script src="https://your-domain/c/<slug>/embed.js"></script>
```

The loader injects the calendar markup into `#calendar-container` and uses the loaded FullCalendar libraries to render events.

When running behind a reverse proxy, ensure it sets the `X-Forwarded-Proto` header so embedded assets use the correct scheme. If you include the `<script>` tag in the `<head>`, add the `defer` attribute (instead of `async`) to ensure the calendar container exists before the loader executes.


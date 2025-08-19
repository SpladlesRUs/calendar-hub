# calendar-hub

A simple FastAPI application for aggregating and embedding calendars.

## Admin login

Set the `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables. To obtain an admin token, send a `POST` request to `/admin/login` with form fields `username` and `password`. The endpoint returns JSON containing a `token` value.

Include this token in subsequent admin requests either as a query parameter `?token=YOUR_TOKEN` or as an `X-Admin-Token` header. Visiting the site root `/` shows a login form that will authenticate and redirect to the dashboard at `/admin`.


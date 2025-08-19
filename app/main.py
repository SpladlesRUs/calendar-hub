import os
import secrets
from typing import Optional
from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import pathlib
from sqlmodel import SQLModel, Field, Session, create_engine, select
from slugify import slugify
import httpx

PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("CALHUB_DB_PATH", "/data/calhub.db")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
ADMIN_TOKEN: Optional[str] = os.getenv("ADMIN_TOKEN")
ALLOWED_PARENTS = os.getenv("ALLOWED_FRAME_PARENTS", "*")

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

class Calendar(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(index=True, unique=True)
    incoming_ics_url: Optional[str] = None
    primary_color: str = "#0b9444"
    accent_color: str = "#0bd3d3"
    background_color: str = "#ffffff"
    logo_url: Optional[str] = None
    logo_path: Optional[str] = None
    timezone: str = "America/New_York"
    default_view: str = "dayGridMonth"
    is_public: bool = True

def init_db():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

app = FastAPI()

# Static and templates
# Ensure the uploads directory exists before mounting static files
os.makedirs("/data/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="/data/uploads"), name="uploads")

# Global headers to allow embedding from your domains
@app.middleware("http")
async def frame_headers(request: Request, call_next):
    resp = await call_next(request)
    # No X-Frame-Options so embedding works
    resp.headers["Content-Security-Policy"] = f"frame-ancestors {ALLOWED_PARENTS}"
    return resp

@app.get("/health", include_in_schema=False)
async def health():
    return Response(status_code=204)


@app.post("/admin/login")
async def admin_login(username: str = Form(...), password: str = Form(...)):
    global ADMIN_TOKEN
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        ADMIN_TOKEN = secrets.token_urlsafe(32)
        return {"token": ADMIN_TOKEN}
    raise HTTPException(status_code=401, detail="Invalid credentials")

def require_admin(request: Request):
    token = request.headers.get("X-Admin-Token") or request.query_params.get("token")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

def make_slug(text: str) -> str:
    """Create a slug from text using the python-slugify library."""
    return slugify(text)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/admin")

# Admin dashboard
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, session: Session = Depends(get_session)):
    calendars = session.exec(select(Calendar).order_by(Calendar.name)).all()
    token = request.query_params.get("token") or request.headers.get("X-Admin-Token")
    with open("templates/admin.html", "r", encoding="utf-8") as f:
        tpl = f.read()
    form_action = "/admin/create"
    token_param = ""
    if token:
        form_action += f"?token={token}"
        token_param = f"?token={token}"
    table_rows = "".join(
        [
            f"<tr><td>{c.name}</td><td>{c.slug}</td><td>{c.incoming_ics_url or ''}</td>"
            f"<td><a href='/c/{c.slug}/embed' target='_blank'>preview</a> | "
            f"<a href='/admin/embed-code/{c.slug}{token_param}'>code</a> | "
            f"<form method='post' action='/admin/delete/{c.slug}{token_param}' style='display:inline' onsubmit=\"return confirm('Delete calendar?')\"><button type='submit'>delete</button></form></td></tr>"
            for c in calendars
        ]
    )
    return HTMLResponse(
        tpl.replace("{{cal_count}}", str(len(calendars)))
        .replace("{{table_rows}}", table_rows)
        .replace("{{form_action}}", form_action)
    )

@app.post("/admin/create", response_class=HTMLResponse)
async def create_calendar(
    request: Request,
    name: str = Form(...),
    slug: Optional[str] = Form(None),
    incoming_ics_url: Optional[str] = Form(None),
    logo_url: Optional[str] = Form(None),
    logo_file: UploadFile | None = File(None),
    primary_color: str = Form("#0b9444"),
    accent_color: str = Form("#0bd3d3"),
    background_color: str = Form("#ffffff"),
    timezone: str = Form("America/New_York"),
    default_view: str = Form("dayGridMonth"),
    session: Session = Depends(get_session)
):
    require_admin(request)
    s = slug or make_slug(name)
    exists = session.exec(select(Calendar).where(Calendar.slug == s)).first()
    if exists:
        raise HTTPException(status_code=400, detail="Slug already exists")

    saved_logo_url = logo_url
    saved_logo_path = None
    if logo_file and logo_file.filename:
        dest_dir = pathlib.Path("/data/uploads") / s
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / logo_file.filename
        with dest.open("wb") as f:
            f.write(await logo_file.read())
        saved_logo_url = f"/uploads/{s}/{logo_file.filename}"
        saved_logo_path = str(dest)

    cal = Calendar(
        name=name,
        slug=s,
        incoming_ics_url=incoming_ics_url,
        logo_url=saved_logo_url,
        logo_path=saved_logo_path,
        primary_color=primary_color,
        accent_color=accent_color,
        background_color=background_color,
        timezone=timezone,
        default_view=default_view,
        is_public=True,
    )
    session.add(cal)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)

# ICS proxy so browsers can load remote feeds without CORS issues
@app.get("/api/cal/{slug}/ics", response_class=PlainTextResponse)
async def proxy_ics(slug: str, session: Session = Depends(get_session)):
    cal = session.exec(select(Calendar).where(Calendar.slug == slug)).first()
    if not cal or not cal.incoming_ics_url:
        raise HTTPException(status_code=404, detail="Calendar or ICS not found")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(cal.incoming_ics_url, headers={"User-Agent": "CalendarHub"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ICS fetch failed: {e}")
    return PlainTextResponse(content=r.text, media_type="text/calendar")

# Public embed page
@app.get("/c/{slug}/embed", response_class=HTMLResponse)
async def embed(slug: str, session: Session = Depends(get_session)):
    cal = session.exec(select(Calendar).where(Calendar.slug == slug, Calendar.is_public == True)).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")
    with open("templates/embed.html", "r", encoding="utf-8") as f:
        tpl = f.read()
    tpl = (
        tpl.replace("{{NAME}}", cal.name)
        .replace("{{SLUG}}", cal.slug)
        .replace("{{LOGO_URL}}", cal.logo_url or "")
        .replace("{{PRIMARY}}", cal.primary_color)
        .replace("{{ACCENT}}", cal.accent_color)
        .replace("{{BG}}", cal.background_color)
        .replace("{{VIEW}}", cal.default_view)
        .replace("{{TZ}}", cal.timezone)
    )
    return HTMLResponse(tpl)


@app.get("/admin/embed-code/{slug}", response_class=HTMLResponse)
async def embed_code(request: Request, slug: str, session: Session = Depends(get_session)):
    """Return a simple page with the iframe embed snippet for a calendar."""
    require_admin(request)
    cal = session.exec(select(Calendar).where(Calendar.slug == slug)).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")
    base = str(request.base_url).rstrip("/")
    src = f"{base}/c/{cal.slug}/embed"
    code = f"<iframe src=\"{src}\" style=\"border:0;width:100%;height:600px\"></iframe>"
    html = (
        "<html><head><title>Embed Code</title>"
        "<link rel='stylesheet' href='/static/styles.css'></head><body>"
        f"<main class='wrap'><h2>Embed code for {cal.name}</h2>"
        f"<textarea readonly style='width:100%;height:120px'>{code}</textarea>"
        "</main></body></html>"
    )
    return HTMLResponse(html)


@app.post("/admin/delete/{slug}")
async def delete_calendar(request: Request, slug: str, session: Session = Depends(get_session)):
    """Delete a calendar and its uploaded logo."""
    require_admin(request)
    cal = session.exec(select(Calendar).where(Calendar.slug == slug)).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")
    if cal.logo_path:
        try:
            os.remove(cal.logo_path)
        except OSError:
            pass
    session.delete(cal)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)

@app.on_event("startup")
def on_start():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    init_db()

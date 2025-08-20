import os
import secrets
from typing import Optional
from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
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
    text_color: str = "#222222"
    title_color: str = "#ffffff"
    logo_url: Optional[str] = None
    logo_path: Optional[str] = None
    logo_height: int = 40
    timezone: str = "America/New_York"
    desktop_view: str = "dayGridMonth"
    mobile_view: str = "listWeek"
    show_name: bool = True
    is_public: bool = True

def init_db():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# Public login page
@app.get("/", response_class=HTMLResponse)
async def root():
    """Show the admin login form."""
    with open("templates/login.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# Admin dashboard
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, session: Session = Depends(get_session)):
    require_admin(request)
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
            f"<a href='/admin/edit/{c.slug}{token_param}'>edit</a> | "
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
    text_color: str = Form("#222222"),
    title_color: str = Form("#ffffff"),
    timezone: str = Form("America/New_York"),
    desktop_view: str = Form("dayGridMonth"),
    mobile_view: str = Form("listWeek"),
    show_name: bool = Form(False),
    logo_height: int = Form(40),
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
        text_color=text_color,
        title_color=title_color,
        timezone=timezone,
        desktop_view=desktop_view,
        mobile_view=mobile_view,
        show_name=show_name,
        logo_height=logo_height,
        is_public=True,
    )
    session.add(cal)
    session.commit()
    token = request.query_params.get("token") or request.headers.get("X-Admin-Token")
    redirect_url = "/admin"
    if token:
        redirect_url += f"?token={token}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)

# Edit calendar
@app.get("/admin/edit/{slug}", response_class=HTMLResponse)
async def edit_calendar_page(request: Request, slug: str, session: Session = Depends(get_session)):
    require_admin(request)
    cal = session.exec(select(Calendar).where(Calendar.slug == slug)).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")
    token = request.query_params.get("token") or request.headers.get("X-Admin-Token")
    token_param = f"?token={token}" if token else ""
    with open("templates/edit.html", "r", encoding="utf-8") as f:
        tpl = f.read()
    def sel(val: str, target: str) -> str:
        return "selected" if val == target else ""
    tpl = (
        tpl.replace("{{SLUG}}", cal.slug)
        .replace("{{NAME}}", cal.name)
        .replace("{{ICS}}", cal.incoming_ics_url or "")
        .replace("{{TZ}}", cal.timezone)
        .replace("{{PRIMARY}}", cal.primary_color)
        .replace("{{ACCENT}}", cal.accent_color)
        .replace("{{BG}}", cal.background_color)
        .replace("{{TEXT_COLOR}}", cal.text_color)
        .replace("{{TITLE_COLOR}}", cal.title_color)
        .replace("{{LOGO_URL}}", cal.logo_url or "")
        .replace("{{LOGO_HEIGHT}}", str(cal.logo_height))
        .replace("{{DESKTOP_MONTH}}", sel(cal.desktop_view, "dayGridMonth"))
        .replace("{{DESKTOP_WEEK}}", sel(cal.desktop_view, "timeGridWeek"))
        .replace("{{DESKTOP_DAY}}", sel(cal.desktop_view, "timeGridDay"))
        .replace("{{DESKTOP_LIST}}", sel(cal.desktop_view, "listWeek"))
        .replace("{{MOBILE_MONTH}}", sel(cal.mobile_view, "dayGridMonth"))
        .replace("{{MOBILE_WEEK}}", sel(cal.mobile_view, "timeGridWeek"))
        .replace("{{MOBILE_DAY}}", sel(cal.mobile_view, "timeGridDay"))
        .replace("{{MOBILE_LIST}}", sel(cal.mobile_view, "listWeek"))
        .replace("{{SHOW_NAME_CHECKED}}", "checked" if cal.show_name else "")
        .replace("{{TOKEN_PARAM}}", token_param)
    )
    return HTMLResponse(tpl)


@app.post("/admin/edit/{slug}")
async def edit_calendar(
    request: Request,
    slug: str,
    name: str = Form(...),
    incoming_ics_url: Optional[str] = Form(None),
    logo_url: Optional[str] = Form(None),
    logo_file: UploadFile | None = File(None),
    primary_color: str = Form("#0b9444"),
    accent_color: str = Form("#0bd3d3"),
    background_color: str = Form("#ffffff"),
    text_color: str = Form("#222222"),
    title_color: str = Form("#ffffff"),
    timezone: str = Form("America/New_York"),
    desktop_view: str = Form("dayGridMonth"),
    mobile_view: str = Form("listWeek"),
    show_name: bool = Form(False),
    logo_height: int = Form(40),
    session: Session = Depends(get_session),
):
    require_admin(request)
    cal = session.exec(select(Calendar).where(Calendar.slug == slug)).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

    cal.name = name
    cal.incoming_ics_url = incoming_ics_url
    cal.timezone = timezone
    cal.desktop_view = desktop_view
    cal.mobile_view = mobile_view
    cal.primary_color = primary_color
    cal.accent_color = accent_color
    cal.background_color = background_color
    cal.text_color = text_color
    cal.title_color = title_color
    cal.show_name = show_name
    cal.logo_height = logo_height

    if logo_file and logo_file.filename:
        dest_dir = pathlib.Path("/data/uploads") / cal.slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / logo_file.filename
        with dest.open("wb") as f:
            f.write(await logo_file.read())
        if cal.logo_path:
            try:
                os.remove(cal.logo_path)
            except OSError:
                pass
        cal.logo_url = f"/uploads/{cal.slug}/{logo_file.filename}"
        cal.logo_path = str(dest)
    elif logo_url is not None:
        cal.logo_url = logo_url or None

    session.add(cal)
    session.commit()
    token = request.query_params.get("token") or request.headers.get("X-Admin-Token")
    redirect_url = "/admin"
    if token:
        redirect_url += f"?token={token}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
# ICS proxy so browsers can load remote feeds without CORS issues
@app.api_route("/api/cal/{slug}/ics", methods=["GET", "HEAD"], response_class=PlainTextResponse)
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
    logo_html = (
        f"<img class='logo' src=\"{cal.logo_url}\" alt=\"{cal.name} logo\">"
        if cal.logo_url
        else ""
    )
    title_html = f"<h1 style='margin-left:8px'>{cal.name}</h1>" if cal.show_name else ""
    tpl = (
        tpl.replace("{{LOGO}}", logo_html)
        .replace("{{TITLE}}", title_html)
        .replace("{{TITLE_TEXT}}", cal.name)
        .replace("{{SLUG}}", cal.slug)
        .replace("{{PRIMARY}}", cal.primary_color)
        .replace("{{ACCENT}}", cal.accent_color)
        .replace("{{BG}}", cal.background_color)
        .replace("{{TEXT_COLOR}}", cal.text_color)
        .replace("{{TITLE_COLOR}}", cal.title_color)
        .replace("{{DESKTOP_VIEW}}", cal.desktop_view)
        .replace("{{MOBILE_VIEW}}", cal.mobile_view)
        .replace("{{TZ}}", cal.timezone)
        .replace("{{LOGO_HEIGHT}}", str(cal.logo_height))
    )
    return HTMLResponse(tpl)


@app.get("/c/{slug}/embed.js", response_class=PlainTextResponse)
async def embed_script(request: Request, slug: str, session: Session = Depends(get_session)):
    """Return a JS loader that renders the calendar into #calendar-container."""
    cal = session.exec(
        select(Calendar).where(Calendar.slug == slug, Calendar.is_public == True)
    ).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    base = f"{scheme}://{host}".rstrip("/")
    logo_url = ""
    if cal.logo_url:
        if cal.logo_url.startswith("http://") or cal.logo_url.startswith("https://"):
            logo_url = cal.logo_url
        else:
            logo_url = f"{base}{cal.logo_url}"

    logo_html = (
        f'<img class="logo" src="{logo_url}" alt="{cal.name} logo">' if logo_url else ""
    )
    title_html = f"<h1 style='margin-left:8px'>{cal.name}</h1>" if cal.show_name else ""
    ics_url = f"{base}/api/cal/{cal.slug}/ics"

    js = f"""
(function(){{
  const loadStyle = href => new Promise((res, rej) => {{
    if (document.querySelector(`link[href="${{href}}"]`)) return res();
    const l=document.createElement('link');
    l.rel='stylesheet';
    l.href=href;
    l.onload=res;
    l.onerror=rej;
    document.head.appendChild(l);
  }});
  const loadScript = src => new Promise((res, rej) => {{
    if (document.querySelector(`script[src="${{src}}"]`)) return res();
    const s=document.createElement('script');
    s.src=src;
    s.onload=res;
    s.onerror=rej;
    document.head.appendChild(s);
  }});

  async function init(){{
    await loadStyle('https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.css');
    await loadStyle('{base}/static/styles.css');
    await loadScript('https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js');
    await loadScript('https://cdn.jsdelivr.net/npm/@fullcalendar/icalendar@6.1.15/index.global.min.js');

    const container=document.getElementById('calendar-container');
    if(!container) return;
    container.innerHTML=`<div class='wrap'><header class=\"bar\"><div class=\"left\">{logo_html}{title_html}</div><div class=\"muted\">{cal.timezone}</div></header><div id=\"calendar\"></div></div>`;
    const style=document.createElement('style');
    style.textContent=`#calendar-container{{--primary:{cal.primary_color};--accent:{cal.accent_color};--bg:{cal.background_color};--text:{cal.text_color};--title:{cal.title_color};}}#calendar-container .logo{{height:{cal.logo_height}px;object-fit:contain}}`;
    document.head.appendChild(style);
    const el=container.querySelector('#calendar');
    const isMobile=window.matchMedia('(max-width: 768px)').matches;
    const initialView=isMobile?'{cal.mobile_view}':'{cal.desktop_view}';
    const headerDesktop={{ left:'prev,next today', center:'title', right:'dayGridMonth,timeGridWeek,timeGridDay,listWeek' }};
    const headerMobile={{ center:'title', left:'', right:'' }};
    const footerMobile={{ left:'prev,next today', right:'dayGridMonth,timeGridWeek,timeGridDay,listWeek' }};
    const colors=getComputedStyle(container);
    const calendar=new FullCalendar.Calendar(el,{{themeSystem:'standard',initialView,headerToolbar:isMobile?headerMobile:headerDesktop,footerToolbar:isMobile?footerMobile:undefined,height:'auto',nowIndicator:true,eventDisplay:'block',eventColor:colors.getPropertyValue('--primary'),eventBorderColor:colors.getPropertyValue('--accent'),eventTextColor:colors.getPropertyValue('--text'),timeZone:'{cal.timezone}',events:[{{url:'{ics_url}',format:'ics'}}]}});
    calendar.render();
    }}
    if (document.readyState === 'loading') {{
      document.addEventListener('DOMContentLoaded', init);
    }} else {{
      init();
    }}
  }})();
  """
    return PlainTextResponse(js, media_type="text/javascript")


@app.get("/admin/embed-code/{slug}", response_class=HTMLResponse)
async def embed_code(request: Request, slug: str, session: Session = Depends(get_session)):
    """Return a page with embed snippets for a calendar."""
    require_admin(request)
    cal = session.exec(select(Calendar).where(Calendar.slug == slug)).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    base = f"{scheme}://{host}".rstrip("/")
    iframe_src = f"{base}/c/{cal.slug}/embed"
    iframe_code = (
        f"<iframe src=\"{iframe_src}\" style=\"border:0;width:100%;height:600px\"></iframe>"
    )
    script_src = f"{base}/c/{cal.slug}/embed.js"
    js_code = (
        f"<div id=\"calendar-container\"></div><script src=\"{script_src}\"></script>"
    )
    html = (
        "<html><head><title>Embed Code</title>"
        "<link rel='stylesheet' href='/static/styles.css'></head><body>"
        f"<main class='wrap'><h2>Embed code for {cal.name}</h2>"
        "<h3>Iframe</h3>"
        f"<textarea readonly style='width:100%;height:120px'>{iframe_code}</textarea>"
        "<h3>JavaScript</h3>"
        f"<textarea readonly style='width:100%;height:120px'>{js_code}</textarea>"
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
    token = request.query_params.get("token") or request.headers.get("X-Admin-Token")
    redirect_url = "/admin"
    if token:
        redirect_url += f"?token={token}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)

@app.on_event("startup")
def on_start():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    init_db()

from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from html import escape
import secrets
from db import SessionLocal
from sqlalchemy.orm import Session
from sqlalchemy import select, func, text
from schemas import LanguageStat, EventOut, RepoOut, EventType
from models import RepositoryLanguage, Event, Repository
from config import settings
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("dev_insights")

app = FastAPI()

security = HTTPBasic()
def require_dashboard_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    user_ok = secrets.compare_digest(credentials.username, "admin")
    pass_ok = secrets.compare_digest(credentials.password, settings.api_key)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )

@app.get("/dashboard", response_class=HTMLResponse,
         dependencies=[Depends(require_dashboard_auth)])
def dashboard(session: Session = Depends(get_db)):
    repos = session.execute(
        select(Repository.full_name, Repository.description,
               Repository.primary_language, Repository.pushed_at)
        .order_by(Repository.pushed_at.desc())
    ).all()
    events = session.execute(
        select(Event.type, Event.repo_name, Event.created_at)
        .order_by(Event.created_at.desc())
    ).all()
    languages = session.execute(
        select(RepositoryLanguage.language,
               func.sum(RepositoryLanguage.bytes).label("bytes"))
        .group_by(RepositoryLanguage.language)
        .order_by(func.sum(RepositoryLanguage.bytes).desc())
    ).all()

    def cell(value) -> str:
        return escape(str(value)) if value is not None else "\u2014"

    repo_rows = "".join(
        f"<tr><td>{cell(r.full_name)}</td><td>{cell(r.description)}</td>"
        f"<td>{cell(r.primary_language)}</td><td>{cell(r.pushed_at)}</td></tr>"
        for r in repos
    )
    event_rows = "".join(
        f"<tr><td>{cell(e.type)}</td><td>{cell(e.repo_name)}</td>"
        f"<td>{cell(e.created_at)}</td></tr>"
        for e in events
    )
    lang_rows = "".join(
        f"<tr><td>{cell(l.language)}</td><td>{cell(l.bytes)}</td></tr>"
        for l in languages
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>dev-insights</title>
  <style>
    :root {{ --bg:#0f1115; --card:#171a21; --line:#262b36; --text:#e6e8ee; --muted:#9aa3b2; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; padding:2.5rem 1.25rem; background:var(--bg); color:var(--text);
            font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
    .wrap {{ max-width: 960px; margin: 0 auto; }}
    header h1 {{ margin:0; font-size:1.6rem; letter-spacing:-0.02em; }}
    header p {{ margin:.35rem 0 0; color:var(--muted); }}
    section {{ margin-top:2.25rem; }}
    section h2 {{ font-size:1rem; text-transform:uppercase; letter-spacing:.08em;
                  color:var(--muted); margin:0 0 .6rem; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
    table {{ border-collapse:collapse; width:100%; }}
    th, td {{ padding:.7rem .9rem; text-align:left; font-size:.9rem; border-bottom:1px solid var(--line); }}
    th {{ color:var(--muted); font-weight:600; background:#12151c; }}
    tr:last-child td {{ border-bottom:none; }}
    tbody tr:hover {{ background:#1c2028; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>dev-insights</h1>
      <p>GitHub developer activity snapshot</p>
    </header>

    <section>
      <h2>Repositories</h2>
      <div class="card"><table>
        <thead><tr><th>Repo</th><th>Description</th><th>Language</th><th>Last pushed</th></tr></thead>
        <tbody>{repo_rows}</tbody>
      </table></div>
    </section>

    <section>
      <h2>Recent events</h2>
      <div class="card"><table>
        <thead><tr><th>Type</th><th>Repo</th><th>When</th></tr></thead>
        <tbody>{event_rows}</tbody>
      </table></div>
    </section>

    <section>
      <h2>Languages by bytes</h2>
      <div class="card"><table>
        <thead><tr><th>Language</th><th>Bytes</th></tr></thead>
        <tbody>{lang_rows}</tbody>
      </table></div>
    </section>
  </div>
</body>
</html>"""

@app.on_event("startup")
def on_startup() -> None:
    from db import init_db
    init_db()
    logger.info("application startup complete; database initialized")

def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid Api Key") 
    
def get_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@app.get("/health")
def health(session: Session = Depends(get_db)):
    try:
         session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check failed: database unreachable")
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok"}

@app.get("/languages", response_model=list[LanguageStat])
def get_languages(session: Session = Depends(get_db), _ = Depends(require_api_key)):
    stmt= select(RepositoryLanguage.language,
                  func.sum(RepositoryLanguage.bytes).label("bytes")).group_by(RepositoryLanguage.language).order_by(func.sum(RepositoryLanguage.bytes).desc()
                )
    rows = session.execute(stmt).all()
    return [LanguageStat(language=row.language, bytes=row.bytes) for row in rows]

@app.get("/events", response_model=list[EventOut])
def get_events(type: EventType | None = None, session: Session = Depends(get_db), _ = Depends(require_api_key)):
    stmt = select(Event.id, Event.type, Event.repo_name, Event.created_at).order_by(Event.created_at.desc())
    if type is not None:
        stmt = stmt.where(Event.type == type)
    rows = session.execute(stmt).all()
    return [EventOut(id=row.id, type=row.type, repo_name=row.repo_name, created_at = row.created_at) for row in rows]

@app.get("/repos", response_model=list[RepoOut])
def get_repos(session: Session = Depends(get_db), _ = Depends(require_api_key)):
    stmt = select(Repository.full_name, Repository.description, Repository.primary_language, Repository.pushed_at).order_by(Repository.pushed_at.desc())
    rows = session.execute(stmt).all()
    return [RepoOut(full_name=row.full_name, description=row.description, primary_language=row.primary_language, pushed_at=row.pushed_at) for row in rows]
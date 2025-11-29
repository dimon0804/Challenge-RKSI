from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, case
from sqlalchemy.orm import sessionmaker, Session
import os
from passlib.context import CryptContext
import time
from typing import Optional
import json
from starlette.middleware.sessions import SessionMiddleware
from fastapi import Query
from fastapi.openapi.utils import get_openapi
import hashlib
from datetime import datetime, timezone, timedelta

# Важно: для демонстрации используется синхронный SQLAlchemy + psycopg2
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://api_user:api_password@localhost:5432/api_challenge")
SECRET_KEY = os.getenv("SECRET_KEY", "dev")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
BRUTE_WINDOW_SECONDS = int(os.getenv("BRUTE_WINDOW_SECONDS", "300"))
BRUTE_MAX_ATTEMPTS = int(os.getenv("BRUTE_MAX_ATTEMPTS", "5"))
ADMIN_INITIAL_PASSWORD = os.getenv("ADMIN_INITIAL_PASSWORD", "admin123")

# Шаблоны для мини-админки
templates = Jinja2Templates(directory="app/templates")

# Инициализация приложения до описания маршрутов
app = FastAPI(
    title="RKSI API Challenge",
    description="Учебный API‑челлендж. Используйте заголовок X-API-Key для защищённых запросов.",
    version="1.0.0",
)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Простые in-memory лимитеры (для учебных целей)
_rate_buckets = {}
_brute_force = {}

MSK_TZ = timezone(timedelta(hours=3))


def format_dt_msk(dt: Optional[datetime]) -> str:
    """Форматирование datetime в строку в часовом поясе МСК (UTC+3)."""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        dt_msk = dt.astimezone(MSK_TZ)
    except Exception:
        dt_msk = dt
    return dt_msk.strftime("%Y-%m-%d %H:%M:%S")

# ----- Импорт ORM-моделей -----
from app.models.base import Base  # type: ignore
from app.models.user import User  # type: ignore
from app.models.api_key import ApiKey  # type: ignore
from app.models.challenge import Challenge  # type: ignore
from app.models.endpoint import Endpoint  # type: ignore
from app.models.attempt import Attempt  # type: ignore
from app.models.log import LogEntry  # type: ignore

# ----- Схемы -----
class AuthRequest(BaseModel):
    username: str
    password: str

class AuthResponse(BaseModel):
    api_key: str

class SubmitRequest(BaseModel):
    endpoint_id: int
    value: str

class BotRegisterRequest(BaseModel):
    username: str
    password: str

# ----- Утилиты -----

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)


def rate_limited(ip: str, limit_per_min: int) -> bool:
    now = int(time.time())
    window = now // 60
    key = f"{ip}:{window}"
    _rate_buckets.setdefault(key, 0)
    _rate_buckets[key] += 1
    return _rate_buckets[key] > limit_per_min


def brute_guard(key: str) -> bool:
    # True если заблокировано
    now = int(time.time())
    rec = _brute_force.get(key)
    if not rec:
        return False
    attempts, ts = rec
    if now - ts > BRUTE_WINDOW_SECONDS:
        _brute_force.pop(key, None)
        return False
    return attempts >= BRUTE_MAX_ATTEMPTS


def brute_fail(key: str):
    now = int(time.time())
    attempts, ts = _brute_force.get(key, (0, now))
    if now - ts > BRUTE_WINDOW_SECONDS:
        attempts = 0
        ts = now
    _brute_force[key] = (attempts + 1, ts)


def brute_reset(key: str):
    _brute_force.pop(key, None)


def require_api_key(request: Request, db: Session) -> User:
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Требуется API-ключ")
    key_obj: Optional[ApiKey] = db.query(ApiKey).filter_by(key=api_key, is_active=True).first()
    if not key_obj:
        raise HTTPException(status_code=403, detail="Неверный или деактивированный API-ключ")
    user = db.get(User, key_obj.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="Пользователь неактивен")
    return user


# ----- OpenAPI (Swagger) customization: X-API-Key security & hide admin already done -----
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    # Add apiKey security scheme
    components = openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})
    components["XApiKey"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Персональный ключ участника. Получить через бота или POST /challenge/auth",
    }
    # Mark secured operations (endpoint and submit) with security requirement
    for path, ops in openapi_schema.get("paths", {}).items():
        if path.startswith("/challenge/endpoint/") or path == "/challenge/submit":
            for method_obj in ops.values():
                method_obj.setdefault("security", []).append({"XApiKey": []})
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi  # type: ignore[assignment]


# ---------- Endpoints management ----------

@app.get("/admin/endpoints", response_class=HTMLResponse, include_in_schema=False)
def admin_endpoints(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    eps = (
        db.query(Endpoint)
        .order_by(Endpoint.order_index.asc(), Endpoint.id.asc())
        .all()
    )
    challenges = {c.id: c for c in db.query(Challenge).all()}
    return templates.TemplateResponse(
        "admin_endpoints.html",
        {
            "request": request,
            "admin": admin,
            "endpoints": eps,
            "challenges": challenges,
        },
    )


@app.post("/admin/endpoints/action", include_in_schema=False)
async def admin_endpoints_action(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    form = await request.form()
    action = str(form.get("action", ""))
    endpoint_id = int(str(form.get("endpoint_id", "0")) or 0)
    ep = db.get(Endpoint, endpoint_id)
    if not ep:
        return RedirectResponse(url="/admin/endpoints", status_code=302)
    if action == "toggle_active":
        ep.is_active = not bool(ep.is_active)
        db.commit()
        return RedirectResponse(url="/admin/endpoints", status_code=302)
    if action == "save":
        # Разрешаем править prompt и ответ (перехешировать)
        new_prompt = str(form.get("prompt", ep.prompt))
        new_answer = str(form.get("answer", "")).strip()
        new_order = form.get("order_index")
        if new_order is not None and str(new_order).strip().isdigit():
            ep.order_index = int(str(new_order).strip())
        ep.prompt = new_prompt
        if new_answer:
            ep.answer_hash = hashlib.sha256(new_answer.encode()).hexdigest()
        db.commit()
        return RedirectResponse(url="/admin/endpoints", status_code=302)
    return RedirectResponse(url="/admin/endpoints", status_code=302)


@app.post("/admin/endpoints/create", include_in_schema=False)
async def admin_endpoints_create(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    form = await request.form()
    try:
        challenge_id_raw = str(form.get("challenge_id", "")).strip()
        challenge_id = int(challenge_id_raw) if challenge_id_raw.isdigit() else 0
    except Exception:
        challenge_id = 0
    path = str(form.get("path", "")).strip()
    method = str(form.get("method", "GET")).strip().upper() or "GET"
    prompt = str(form.get("prompt", "")).strip()
    answer = str(form.get("answer", "")).strip()
    order_raw = str(form.get("order_index", "")).strip()
    try:
        order_index = int(order_raw) if order_raw.isdigit() else 0
    except Exception:
        order_index = 0

    # Минимальная валидация: требуется challenge_id, path, prompt и answer
    if not challenge_id or not path or not prompt or not answer:
        return RedirectResponse(url="/admin/endpoints", status_code=302)

    answer_hash = hashlib.sha256(answer.encode()).hexdigest()

    ep = Endpoint(
        challenge_id=challenge_id,
        path=path,
        method=method,
        prompt=prompt,
        answer_hash=answer_hash,
        order_index=order_index,
        is_active=True,
    )
    db.add(ep)
    db.commit()
    return RedirectResponse(url="/admin/endpoints", status_code=302)


# ----- Утилиты админ-аутентификации -----
def get_current_admin(request: Request, db: Session) -> Optional[User]:
    admin_user_id = request.session.get("admin_user_id")
    if not admin_user_id:
        return None
    user = db.get(User, admin_user_id)
    if not user or user.role != "admin" or not user.is_active:
        return None
    return user

# Мини-админка: редирект на новый Dashboard
@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_index_redirect(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    return RedirectResponse(url="/admin/dashboard", status_code=302)


@app.get("/admin/dashboard", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    total_users = db.query(User).count()
    active_users = db.query(User).filter_by(is_active=True).count()
    total_attempts = db.query(Attempt).count()
    correct_attempts = db.query(Attempt).filter_by(is_correct=True).count()
    total_logs = db.query(LogEntry).count()
    endpoints_total = db.query(Endpoint).filter_by(is_active=True).count()
    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "admin": admin,
            "total_users": total_users,
            "active_users": active_users,
            "total_attempts": total_attempts,
            "correct_attempts": correct_attempts,
            "total_logs": total_logs,
            "endpoints_total": endpoints_total,
        },
    )


@app.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
def admin_login_form(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})


@app.post("/admin/login", include_in_schema=False)
async def admin_login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    user = db.query(User).filter_by(username=username).first()
    if not user or user.role != "admin" or not user.is_active or not verify_password(password, user.password_hash):
        # Рендерим форму с ошибкой
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Неверные данные или нет прав администратора"}, status_code=401)
    request.session["admin_user_id"] = user.id
    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/logout", include_in_schema=False)
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=302)


@app.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
def admin_users(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    users = db.query(User).order_by(User.created_at.desc()).all()
    # Для каждого пользователя найдём активный ключ
    keys = {u.id: db.query(ApiKey).filter_by(user_id=u.id, is_active=True).first() for u in users}
    return templates.TemplateResponse("admin_users.html", {"request": request, "admin": admin, "users": users, "keys": keys})


@app.post("/admin/users/action", include_in_schema=False)
async def admin_users_action(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    form = await request.form()
    action = str(form.get("action", ""))
    user_id = int(str(form.get("user_id", "0")) or 0)
    user = db.get(User, user_id)
    if not user:
        return RedirectResponse(url="/admin/users", status_code=302)
    if action == "activate":
        user.is_active = True
        db.commit()
    elif action == "deactivate":
        user.is_active = False
        db.commit()
    elif action == "make_admin":
        user.role = "admin"
        db.commit()
    elif action == "revoke_key":
        k = db.query(ApiKey).filter_by(user_id=user.id, is_active=True).first()
        if k:
            k.is_active = False
            db.commit()
    elif action == "create_key":
        import secrets
        # деактивировать старые и создать новый
        for k in db.query(ApiKey).filter_by(user_id=user.id, is_active=True).all():
            k.is_active = False
        newk = ApiKey(user_id=user.id, key=secrets.token_hex(16), is_active=True)
        db.add(newk)
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=302)


@app.get("/admin/leaderboard", response_class=HTMLResponse, include_in_schema=False)
def admin_leaderboard(
    request: Request,
    db: Session = Depends(get_db),
    active_only: Optional[bool] = Query(default=None),
    min_correct: int = Query(default=0, ge=0),
):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    # Подсчёт количества верных решений по пользователям и времени последнего верного
    from sqlalchemy import func
    agg = (
        db.query(
            Attempt.user_id,
            func.sum(case((Attempt.is_correct == True, 1), else_=0)).label("correct_count"),
            func.max(case((Attempt.is_correct == True, Attempt.created_at), else_=None)).label("last_correct_at"),
            func.count(Attempt.id).label("attempts_count"),
        )
        .group_by(Attempt.user_id)
        .all()
    )
    # Собираем данные вместе с именем пользователя
    user_map = {u.id: u for u in db.query(User).all()}
    endpoints_total = db.query(Endpoint).filter_by(is_active=True).count()
    rows = []
    for user_id, correct_count, last_correct_at, attempts_count in agg:
        u = user_map.get(user_id)
        if not u:
            continue
        if active_only is True and not u.is_active:
            continue
        if correct_count is None:
            correct_count = 0
        if correct_count < min_correct:
            continue
        completed_at = last_correct_at if endpoints_total and int(correct_count) >= int(endpoints_total) else None
        rows.append({
            "user": u,
            "correct_count": int(correct_count or 0),
            "last_correct_at": last_correct_at,
            "attempts_count": int(attempts_count or 0),
            "completed_at": completed_at,
        })
    # сортировка: больше correct_count, затем раньше достигнутый (минимальное время последнего верного)
    rows.sort(key=lambda x: (-x["correct_count"], x["completed_at"] or x["last_correct_at"] or time.time()))
    return templates.TemplateResponse(
        "admin_leaderboard.html",
        {
            "request": request,
            "admin": admin,
            "rows": rows,
            "active_only": active_only,
            "min_correct": min_correct,
            "endpoints_total": endpoints_total,
            "format_dt_msk": format_dt_msk,
        },
    )


@app.get("/admin/progress", response_class=HTMLResponse, include_in_schema=False)
def admin_progress(
    request: Request,
    db: Session = Depends(get_db),
    username: Optional[str] = Query(default=None),
    min_solved: int = Query(default=0, ge=0),
):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    endpoints = db.query(Endpoint).filter_by(is_active=True).order_by(Endpoint.order_index.asc(), Endpoint.id.asc()).all()
    users = db.query(User).filter_by(is_active=True).all()
    attempts = db.query(Attempt).all()
    by_user_ep = {}
    for a in attempts:
        key = (a.user_id, a.endpoint_id)
        if key not in by_user_ep:
            by_user_ep[key] = {"attempts": 0, "correct": False}
        by_user_ep[key]["attempts"] += 1
        if a.is_correct:
            by_user_ep[key]["correct"] = True
    rows = []
    for u in users:
        cell_map = {}
        solved = 0
        total_attempts = 0
        for ep in endpoints:
            data = by_user_ep.get((u.id, ep.id))
            if data:
                cell_map[ep.id] = data
                total_attempts += data["attempts"]
                if data["correct"]:
                    solved += 1
        # Фильтрация по имени пользователя
        if username and username.lower() not in u.username.lower():
            continue
        # Фильтрация по минимальному количеству решённых этапов
        if solved < min_solved:
            continue
        rows.append({"user": u, "by_ep": cell_map, "solved": solved, "total_attempts": total_attempts})
    return templates.TemplateResponse(
        "admin_progress.html",
        {
            "request": request,
            "admin": admin,
            "endpoints": endpoints,
            "rows": rows,
            "filter_username": username or "",
            "min_solved": min_solved,
        },
    )

@app.get("/challenge/info", tags=["Challenge"])
def challenge_info(db: Session = Depends(get_db)):
    challenges = db.query(Challenge).filter_by(is_active=True).all()
    return {
        "title": "API Challenge РКСИ",
        "levels": [c.level for c in challenges],
        "count": len(challenges),
        "docs": "/docs",
    }


# ---------- Logs & Export ----------

@app.get("/admin/logs", response_class=HTMLResponse, include_in_schema=False)
def admin_logs(
    request: Request,
    db: Session = Depends(get_db),
    user_id: Optional[str] = Query(default=None),
    event: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    q = db.query(LogEntry)
    if user_id is not None and str(user_id).strip().isdigit():
        q = q.filter(LogEntry.user_id == int(str(user_id).strip()))
    if event:
        q = q.filter(LogEntry.event == event)
    q = q.order_by(LogEntry.created_at.desc()).limit(limit)
    logs = q.all()
    # Карта пользователей
    user_map = {u.id: u for u in db.query(User).all()}
    # Парсим meta как JSON для удобного отображения
    meta_parsed = {}
    for l in logs:
        try:
            meta_parsed[l.id] = json.loads(l.meta) if l.meta else None
        except Exception:
            meta_parsed[l.id] = None
    return templates.TemplateResponse("admin_logs.html", {
        "request": request,
        "admin": admin,
        "logs": logs,
        "user_map": user_map,
        "meta_parsed": meta_parsed,
        "filter_user_id": user_id,
        "filter_event": event or "",
        "limit": limit,
        "format_dt_msk": format_dt_msk,
    })


def _csv_escape(v: str) -> str:
    if v is None:
        return ""
    s = str(v)
    if any(c in s for c in [',', '"', '\n', '\r']):
        return '"' + s.replace('"', '""') + '"'
    return s


@app.get("/admin/export/users.csv", response_class=PlainTextResponse, include_in_schema=False)
def export_users(db: Session = Depends(get_db), request: Request = None):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    rows = ["id,username,role,is_active,created_at"]
    for u in db.query(User).order_by(User.id.asc()).all():
        rows.append(
            f"{u.id},{_csv_escape(u.username)},{u.role},{int(bool(u.is_active))},{_csv_escape(u.created_at)}"
        )
    return PlainTextResponse("\n".join(rows), media_type="text/csv")


@app.get("/admin/export/attempts.csv", response_class=PlainTextResponse, include_in_schema=False)
def export_attempts(db: Session = Depends(get_db), request: Request = None):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    rows = ["id,user_id,username,endpoint_id,is_correct,created_at"]
    user_map = {u.id: u.username for u in db.query(User).all()}
    for a in db.query(Attempt).order_by(Attempt.id.asc()).all():
        rows.append(
            f"{a.id},{a.user_id},{_csv_escape(user_map.get(a.user_id,''))},{a.endpoint_id},{int(bool(a.is_correct))},{_csv_escape(a.created_at)}"
        )
    return PlainTextResponse("\n".join(rows), media_type="text/csv")


@app.get("/admin/export/leaderboard.csv", response_class=PlainTextResponse, include_in_schema=False)
def export_leaderboard(db: Session = Depends(get_db), request: Request = None):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    from sqlalchemy import func
    agg = (
        db.query(
            Attempt.user_id,
            func.sum(func.case((Attempt.is_correct == True, 1), else_=0)).label("correct_count"),
            func.max(func.case((Attempt.is_correct == True, Attempt.created_at), else_=None)).label("last_correct_at"),
        )
        .group_by(Attempt.user_id)
        .all()
    )
    user_map = {u.id: u.username for u in db.query(User).all()}
    rows = ["rank,user_id,username,correct_count,last_correct_at,attempts_count,completed_at"]
    data = []
    endpoints_total = db.query(Endpoint).filter_by(is_active=True).count()
    for user_id, correct_count, last_correct_at in agg:
        attempts_count = db.query(Attempt).filter_by(user_id=user_id).count()
        completed_at = last_correct_at if endpoints_total and int(correct_count or 0) >= int(endpoints_total) else None
        data.append((user_id, int(correct_count or 0), last_correct_at, attempts_count, completed_at))
    data.sort(key=lambda x: (-x[1], x[4] or x[2] or time.time()))
    for i, (uid, count, last_at, atts, comp) in enumerate(data, start=1):
        rows.append(f"{i},{uid},{_csv_escape(user_map.get(uid,''))},{count},{_csv_escape(last_at)},{atts},{_csv_escape(comp)}")
    return PlainTextResponse("\n".join(rows), media_type="text/csv")


@app.get("/admin/export/judges.csv", response_class=PlainTextResponse, include_in_schema=False)
def export_judges(db: Session = Depends(get_db), request: Request = None):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    from sqlalchemy import func
    endpoints_total = db.query(Endpoint).filter_by(is_active=True).count()
    agg = (
        db.query(
            Attempt.user_id,
            func.sum(case((Attempt.is_correct == True, 1), else_=0)).label("correct_count"),
            func.max(case((Attempt.is_correct == True, Attempt.created_at), else_=None)).label("last_correct_at"),
            func.count(Attempt.id).label("attempts_count"),
        )
        .group_by(Attempt.user_id)
        .all()
    )
    user_map = {u.id: u.username for u in db.query(User).all()}
    rows = ["rank,username,correct_count,attempts,completed_at"]
    data = []
    for user_id, correct_count, last_correct_at, attempts_count in agg:
        completed_at = last_correct_at if endpoints_total and int(correct_count or 0) >= int(endpoints_total) else None
        data.append((user_id, user_map.get(user_id, ''), int(correct_count or 0), int(attempts_count or 0), completed_at))
    data.sort(key=lambda x: (-x[2], x[4] or time.time()))
    for i, (uid, uname, cc, atts, comp) in enumerate(data, start=1):
        rows.append(f"{i},{_csv_escape(uname)},{cc},{atts},{_csv_escape(comp)}")
    return PlainTextResponse("\n".join(rows), media_type="text/csv")




@app.get("/admin/user_diagnostics", response_class=HTMLResponse, include_in_schema=False)
def admin_user_diagnostics(
    request: Request,
    db: Session = Depends(get_db),
    username: Optional[str] = Query(default=None),
):
    admin = get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)

    filter_username = (username or "").strip()
    user = None
    endpoints = []
    by_ep = {}
    logs = []
    meta_parsed = {}

    if filter_username:
        user = db.query(User).filter(User.username.ilike(f"%{filter_username}%")).order_by(User.id.asc()).first()

    if user:
        # Этапы
        endpoints = db.query(Endpoint).filter_by(is_active=True).order_by(Endpoint.order_index.asc(), Endpoint.id.asc()).all()
        # Попытки по этапам
        attempts = db.query(Attempt).filter_by(user_id=user.id).all()
        tmp = {}
        for a in attempts:
            data = tmp.setdefault(a.endpoint_id, {"attempts": 0, "correct": False})
            data["attempts"] += 1
            if a.is_correct:
                data["correct"] = True
        by_ep = tmp

        # Логи пользователя
        logs = (
            db.query(LogEntry)
            .filter(LogEntry.user_id == user.id)
            .order_by(LogEntry.created_at.desc())
            .limit(200)
            .all()
        )
        for l in logs:
            try:
                meta_parsed[l.id] = json.loads(l.meta) if l.meta else None
            except Exception:
                meta_parsed[l.id] = None

    return templates.TemplateResponse(
        "admin_user_diagnostics.html",
        {
            "request": request,
            "admin": admin,
            "user": user,
            "endpoints": endpoints,
            "by_ep": by_ep,
            "logs": logs,
            "meta_parsed": meta_parsed,
            "filter_username": filter_username,
            "format_dt_msk": format_dt_msk,
        },
    )


@app.post("/challenge/auth", response_model=AuthResponse, tags=["Challenge"])
def challenge_auth(payload: AuthRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if rate_limited(ip, RATE_LIMIT_PER_MINUTE):
        db.add(
            LogEntry(
                user_id=None,
                event="auth",
                meta=json.dumps({
                    "username": payload.username,
                    "ok": False,
                    "reason": "rate_limited",
                    "ip": ip,
                    "path": request.url.path,
                    "method": request.method,
                }),
            )
        )
        db.commit()
        raise HTTPException(429, detail="Слишком много запросов, попробуйте позже")

    if brute_guard(payload.username):
        db.add(
            LogEntry(
                user_id=None,
                event="auth",
                meta=json.dumps({
                    "username": payload.username,
                    "ok": False,
                    "reason": "brute_block",
                    "ip": ip,
                    "path": request.url.path,
                    "method": request.method,
                }),
            )
        )
        db.commit()
        raise HTTPException(429, detail="Слишком много попыток, подождите")

    user = db.query(User).filter_by(username=payload.username).first()
    if not user:
        brute_fail(payload.username)
        db.add(
            LogEntry(
                user_id=None,
                event="auth",
                meta=json.dumps({
                    "username": payload.username,
                    "ok": False,
                    "reason": "user_not_found",
                    "ip": ip,
                    "path": request.url.path,
                    "method": request.method,
                }),
            )
        )
        db.commit()
        raise HTTPException(401, detail="Неверные учетные данные")

    if not verify_password(payload.password, user.password_hash):
        brute_fail(payload.username)
        db.add(
            LogEntry(
                user_id=user.id,
                event="auth",
                meta=json.dumps({
                    "username": payload.username,
                    "user_id": user.id,
                    "ok": False,
                    "reason": "bad_password",
                    "ip": ip,
                    "path": request.url.path,
                    "method": request.method,
                }),
            )
        )
        db.commit()
        raise HTTPException(401, detail="Неверные учетные данные")

    brute_reset(payload.username)

    key = db.query(ApiKey).filter_by(user_id=user.id, is_active=True).first()
    if not key:
        # создать новый ключ
        import secrets
        key_value = secrets.token_hex(16)
        key = ApiKey(user_id=user.id, key=key_value, is_active=True)
        db.add(key)
        db.commit()
        db.refresh(key)

    db.add(
        LogEntry(
            user_id=user.id,
            event="auth",
            meta=json.dumps({
                "username": user.username,
                "user_id": user.id,
                "ok": True,
                "reason": "ok",
                "ip": ip,
                "path": request.url.path,
                "method": request.method,
            }),
        )
    )
    db.commit()

    return {"api_key": key.key}


@app.get("/challenge/endpoint/{endpoint_id}", tags=["Challenge"])
def get_endpoint(endpoint_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_api_key(request, db)
    ep = db.query(Endpoint).filter_by(id=endpoint_id, is_active=True).first()
    if not ep:
        raise HTTPException(404, detail="Этап не найден")
    # Проверка последовательного прохождения: нельзя открыть этап,
    # если не решены все предыдущие по order_index
    prev_eps = (
        db.query(Endpoint)
        .filter(Endpoint.is_active == True, Endpoint.order_index < ep.order_index)
        .all()
    )
    if prev_eps:
        completed_ids = {
            a.endpoint_id
            for a in db.query(Attempt).filter_by(user_id=user.id, is_correct=True).all()
        }
        for prev in prev_eps:
            if prev.id not in completed_ids:
                raise HTTPException(
                    status_code=403,
                    detail="Этот этап станет доступен после прохождения предыдущих этапов",
                )
    # Логируем
    ip = request.client.host if request.client else "unknown"
    db.add(
        LogEntry(
            user_id=user.id,
            event="view_endpoint",
            meta=json.dumps(
                {
                    "endpoint_id": endpoint_id,
                    "path": request.url.path,
                    "method": request.method,
                    "ip": ip,
                    "user_id": user.id,
                    "username": user.username,
                }
            ),
        )
    )
    db.commit()
    return {"endpoint_id": ep.id, "prompt": ep.prompt}


@app.post("/challenge/submit", tags=["Challenge"])
def submit_answer(payload: SubmitRequest, request: Request, db: Session = Depends(get_db)):
    user = require_api_key(request, db)
    ep = db.query(Endpoint).filter_by(id=payload.endpoint_id, is_active=True).first()
    if not ep:
        raise HTTPException(404, detail="Этап не найден")

    import hashlib
    is_correct = hashlib.sha256(payload.value.strip().encode()).hexdigest() == ep.answer_hash

    db.add(Attempt(user_id=user.id, endpoint_id=ep.id, submitted_value=payload.value, is_correct=is_correct))

    # Логируем детально
    ip = request.client.host if request.client else "unknown"
    value_preview = payload.value[:200]
    db.add(
        LogEntry(
            user_id=user.id,
            event="submit",
            meta=json.dumps(
                {
                    "endpoint_id": ep.id,
                    "ok": is_correct,
                    "path": request.url.path,
                    "method": request.method,
                    "ip": ip,
                    "user_id": user.id,
                    "username": user.username,
                    "value_preview": value_preview,
                }
            ),
        )
    )
    db.commit()

    return {"ok": is_correct}


# Скрытый эндпоинт регистрации для бота
@app.post("/bot/register", include_in_schema=False)
def bot_register(payload: BotRegisterRequest, db: Session = Depends(get_db)):
    if len(payload.password) < 6:
        raise HTTPException(400, detail="Пароль слишком короткий")
    existing = db.query(User).filter_by(username=payload.username).first()
    if existing:
        return {"status": "exists"}
    user = User(username=payload.username, password_hash=hash_password(payload.password), role="participant", is_active=True)
    db.add(user)
    db.add(LogEntry(user_id=None, event="bot_register", meta=json.dumps({"username": payload.username})))
    db.commit()
    return {"status": "created"}


# Сервисная инициализация: создать админа при первом запуске (если нет)
@app.on_event("startup")
def ensure_admin():
    # Ждём готовность БД (простейший retry)
    max_wait = 30
    for i in range(max_wait):
        try:
            db = SessionLocal()
            # Пробный запрос
            db.execute("SELECT 1")
            break
        except Exception:
            time.sleep(1)
        finally:
            try:
                db.close()
            except Exception:
                pass

    db = SessionLocal()
    try:
        admin = db.query(User).filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", password_hash=hash_password(ADMIN_INITIAL_PASSWORD), role="admin", is_active=True)
            db.add(admin)
            db.commit()

        # Сидирование демо-данных (челленджи и этапы), если нет
        if db.query(Challenge).count() == 0:
            ch1 = Challenge(title="Легкий старт", level="easy", description="Найдите кодовое слово на первой странице", is_active=True)
            ch2 = Challenge(title="Средний уровень", level="medium", description="Поработайте с эндпоинтом и подсказками", is_active=True)
            ch3 = Challenge(title="Финал", level="hard", description="Финальная проверка", is_active=True)
            db.add_all([ch1, ch2, ch3])
            db.commit()

            import hashlib
            def ah(s: str) -> str:
                return hashlib.sha256(s.encode()).hexdigest()

            e1 = Endpoint(challenge_id=ch1.id, path="/challenge/endpoint/1", method="GET", prompt="Кодовое слово: rksi", answer_hash=ah("rksi"), order_index=1, is_active=True)
            e2 = Endpoint(challenge_id=ch2.id, path="/challenge/endpoint/2", method="GET", prompt="Сложите 2+2 и запишите ответ", answer_hash=ah("4"), order_index=2, is_active=True)
            e3 = Endpoint(challenge_id=ch3.id, path="/challenge/endpoint/3", method="GET", prompt="Финал: слово 'victory'", answer_hash=ah("victory"), order_index=3, is_active=True)
            db.add_all([e1, e2, e3])
            db.commit()
    finally:
        db.close()

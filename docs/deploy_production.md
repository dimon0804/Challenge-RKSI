# Прод‑деплой API Challenge РКСИ

Этот документ описывает, как развернуть проект **API Challenge РКСИ** на новом сервере для проведения конкурса.

Исходные данные:

- Внешний IP сервера: **217.198.5.205**.
- Проект разворачивается через **Docker Compose**.
- Сервисы:
  - `backend` — FastAPI + PostgreSQL (SQLAlchemy).
  - `db` — PostgreSQL 15.
  - `bot` — Telegram‑бот (aiogram).
  - `adminer` — веб‑админка для БД.
  - `nginx` — фронтовый веб‑сервер (проксирует на backend).

---

## 1. Подготовка сервера

Требования (минимум):

- ОС: свежий Linux (Ubuntu Server 22.04+ или аналог).
- Ресурсы: **2 vCPU, 2–4 ГБ RAM, 20+ ГБ диска** хватит с запасом для ~200 участников.
- Открытые порты снаружи:
  - `80/tcp` — HTTP (Nginx, основной вход для участников и админки).
  - `22/tcp` — SSH (по возможности ограничить по IP).
  - `8080/tcp` — по желанию, если нужен внешний доступ к Adminer.

### Установка Docker и Docker Compose

Под root или через `sudo`:

```bash
curl -fsSL https://get.docker.com | sh

# Проверка
docker --version
# Плагин compose обычно устанавливается вместе
docker compose version
```

---

## 2. Клонирование проекта

Выбираем директорию, например `/opt/rksi_challenge`:

```bash
cd /opt
sudo mkdir -p rksi_challenge
sudo chown "$(whoami)":"$(whoami)" rksi_challenge
cd rksi_challenge

# Клонируем репозиторий (URL заменить на свой)
git clone <URL_РЕПОЗИТОРИЯ> .

cd api-challenge
```

Убедитесь, что в каталоге есть файлы `docker-compose.yml`, `.env.sample`, `backend/`, `bot/`, `infra/` и т.д.

---

## 3. Настройка `.env` для продакшена

Создаём `.env` на основе `.env.sample` (если ещё не создан):

```bash
cp .env.sample .env
```

Открываем `.env` и настраиваем значения. Пример боевой конфигурации:

```env
# Backend
SECRET_KEY=сложный_секретный_ключ_для_fastapi
DB_HOST=db
DB_PORT=5432
DB_NAME=api_challenge
DB_USER=api_user
DB_PASSWORD=<сильный_пароль>
DATABASE_URL=postgresql+psycopg2://api_user:<сильный_пароль>@db:5432/api_challenge
ADMIN_INITIAL_PASSWORD=<пароль_админа_в_админке>
RATE_LIMIT_PER_MINUTE=60
BRUTE_WINDOW_SECONDS=300
BRUTE_MAX_ATTEMPTS=5

# Bot
BOT_TOKEN=<боевой_Telegram_бот_токен>
BOT_ADMIN_CHAT_ID=<твой_telegram_chat_id_числом>
BACKEND_BASE_URL=http://backend:8000
BOT_LOG_LEVEL=INFO
BOT_DB_PATH=/app/bot_storage.db

# Nginx
PUBLIC_DOMAIN=217.198.5.205
```

Важно:

- `SECRET_KEY` — любая длинная случайная строка.
- `DB_PASSWORD` и `ADMIN_INITIAL_PASSWORD` должны отличаться от локальных тестовых.
- `BOT_TOKEN` — токен из BotFather.
- `BOT_ADMIN_CHAT_ID` — твой chat_id (число, без кавычек), чтобы `/broadcast` и админ‑команды были только у тебя.
- `BACKEND_BASE_URL` **оставляем** `http://backend:8000`, чтобы бот ходил к backend внутри docker‑сети.
- `PUBLIC_DOMAIN` — IP сервера, по которому квест будут открывать участники.

---

## 4. Запуск инфраструктуры через Docker Compose

Из директории `api-challenge`:

```bash
docker compose up -d --build
```

Это поднимет контейнеры:

- `db` — PostgreSQL.
- `adminer` — веб‑доступ к БД на порту 8080.
- `backend` — FastAPI API на 8000 внутри сети.
- `bot` — Telegram‑бот.
- `nginx` — фронт на 80 порту.

Проверка состояния:

```bash
docker compose ps
```

У всех сервисов статус должен быть `running` или `Up`.

---

## 5. Применение миграций БД

После первого запуска нужно накатить миграции Alembic:

```bash
docker compose exec backend bash -lc "alembic upgrade head"
```

Это создаст все таблицы (users, api_keys, challenges, endpoints, attempts, logs и т.д.) и засеет демо‑данные (включая админа и стартовые этапы, если БД была пустая).

---

## 6. Проверка работы сервиса

### 6.1. API и админка

С любого клиента (браузер, curl):

- Swagger/OpenAPI:  
  `http://217.198.5.205/docs`

- Админ‑панель логина:  
  `http://217.198.5.205/admin/login`

Логин/пароль администратора:

- username: `admin`
- password: из `ADMIN_INITIAL_PASSWORD` в `.env`.

После входа доступны:

- Dashboard: `/admin/dashboard`
- Users: `/admin/users`
- Endpoints: `/admin/endpoints`
- Progress: `/admin/progress`
- Logs: `/admin/logs`

### 6.2. Adminer (опционально)

Если нужен доступ к БД через веб‑интерфейс:

- URL: `http://217.198.5.205:8080`
- System: `PostgreSQL`
- Server: `db`
- User: `DB_USER` из `.env`
- Password: `DB_PASSWORD` из `.env`
- Database: `DB_NAME` из `.env`

### 6.3. Бот

1. Убедиться, что контейнер `bot` запущен:

   ```bash
   docker compose ps
   ```

2. В Telegram найти бота по имени, указанному при создании в BotFather.

3. Отправить `/start` — бот должен ответить приветственным сообщением.
4. Проверить:
   - «Регистрация» — создаёт пользователя через `/bot/register`.
   - «Получить API-ключ» — через `/challenge/auth`.
   - «Проверить кодовое слово» — работает с `/challenge/submit`.

Если всё это работает, прод‑инстанс готов к использованию в конкурсе.

---

## 7. Использование рассылки `/broadcast`

Для рассылки сообщений участникам через бота:

1. Убедись, что `BOT_ADMIN_CHAT_ID` в `.env` равен твоему chat_id.
2. Участники должны хотя бы раз написать боту (`/start` или кнопки) — их `chat_id` сохранятся в локальной SQLite‑БД `bot_storage.db` внутри контейнера.
3. От своего аккаунта отправь боту команду:

   ```
   /broadcast Текст сообщения для всех участников
   ```

Бот ответит количеством успешно отправленных сообщений и ошибок.

---

## 8. Рекомендации по безопасности и производительности

1. **Firewall**
   - Разрешить снаружи только нужные порты:
     - 80 (HTTP) и, если будет HTTPS, 443.
     - 22 (SSH) — по возможности ограничить по IP.
   - Порт 8080 (Adminer) можно закрыть внешнему миру и использовать только через SSH‑tunnel.

2. **PostgreSQL и backend**
   - При желании можно убрать прямой проброс портов из `docker-compose.yml`:
     - удалить строки `"5432:5432"` у `db` и `"8000:8000"` у `backend`, чтобы доступ к ним был только через docker‑сеть и Nginx.

3. **HTTPS (опционально)**
   - Для публичного доступа лучше настроить TLS (Let’s Encrypt) на Nginx.
   - Для этого можно использовать `certbot` или отдельный контейнер (например, `nginx-proxy` + `acme-companion`).

4. **Резервное копирование БД**
   - Регулярно делать дамп:

     ```bash
     docker compose exec db pg_dump -U api_user api_challenge > backup.sql
     ```

5. **Логи**
   - При длительной работе можно настроить ограничения на размер логов Docker (`--log-opt max-size` и т.п.) или использовать отдельный лог‑агрегатор.

---

## 9. Краткая памятка организатору

1. Поднять сервер и развернуть проект по шагам 1–6.
2. Проверить админку, бота и работу этапов квеста.
3. Раздать студентам:
   - ссылку/QR на бота;
   - IP сервера: `http://217.198.5.205`;
   - офлайн‑листовки с подсказками.
4. Во время проведения конкурса:
   - следить за `/admin/leaderboard` и `/admin/progress`;
   - использовать `/admin/logs` при разборе спорных ситуаций;
   - при необходимости использовать `/broadcast` для важных объявлений.
5. После завершения — выгрузить результаты экспортами CSV и использовать текст из `docs/final_speech.md` и подготовленных постов для подведения итогов.

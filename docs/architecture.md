# Архитектура API Challenge

- FastAPI backend: REST API + минимальный админ-веб (Jinja2)
- Aiogram бот: регистрация участников, выдача API-ключей, проверка ответов
- PostgreSQL: хранение пользователей, ключей, этапов, попыток, логов
- Adminer: визуальный доступ к БД
- Alembic: миграции схему
- Docker Compose: локальный запуск всех сервисов
- Nginx: обратный прокси (опционально)

Схема ролей: admin, participant. Авторизация — через заголовок `X-API-Key`.

Основные эндпоинты:
- GET /challenge/info
- POST /challenge/auth
- GET /challenge/endpoint/{id}
- POST /challenge/submit

Логирование: таблица logs (who, what, when, meta).
Rate-limit и анти-brute-force — в памяти (учебная реализация).

# API Challenge — РКСИ

Интерактивный командный квест для студентов РКСИ. Стек: FastAPI, aiogram, PostgreSQL, Adminer, Docker, Alembic, pytest. Все интерфейсы и документация — на русском.

## Быстрый старт (локально через Docker Compose)

1. Скопируйте `.env.sample` в `.env` и укажите значения переменных.
2. Запустите: `docker-compose up -d --build`.
3. Примените миграции: `docker-compose exec backend bash -lc "alembic upgrade head"`.
4. Зайдите в Adminer: http://localhost:8080 (System: PostgreSQL, Server: db, User/Password из `.env`).
5. Откройте Swagger: http://localhost:8000/docs
6. Запустите бота: контейнер `bot` стартует автоматически, добавьте его в Telegram по ссылке из `docs/flyer_texts.md`.

## Компоненты

- backend: FastAPI API + админ-веб (Jinja2)
- bot: Telegram-бот (aiogram)
- db: PostgreSQL
- adminer: веб-админка БД
- nginx: обратный прокси (опционально)

## Безопасность

- Секреты и пароли в `.env`
- Пароли хэшируются (bcrypt/passlib)
- API-ключи для участников (выдаёт бот)
- Rate-limit и анти-brute-force в API

## Документация

- docs/architecture.md — архитектура
- docs/student_guide.md — правила и примеры запросов
- docs/admin_guide.md — администрирование, бэкапы, ротация ключей
- docs/flyer_texts.md — тексты для листовок/плакатов/Telegram

## Тесты

- pytest: `docker-compose exec backend bash -lc "pytest -q"`

## Продакшн

- Пример `infra/systemd-unit-example.service`
- Рекомендован reverse-proxy nginx, HTTPS (Let's Encrypt/Certbot)

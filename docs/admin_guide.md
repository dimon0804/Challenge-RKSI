# Руководство администратора

## Создание админа
- При первом запуске создаётся пользователь `admin` с паролем из переменной `ADMIN_INITIAL_PASSWORD`.

## Просмотр состояния
- Откройте http://localhost:8000/admin — количество пользователей, попыток и логов.
- Swagger: http://localhost:8000/docs

## Управление ключами
- Ключи выдаются через /challenge/auth.
- Для временной деактивации ключа установите `api_keys.is_active = false` через Adminer либо добавьте эндпоинт (расширение).

## Бэкапы
- Используйте `scripts/backup.sh` и `scripts/restore.sh` (внутри контейнера db или извне).

## Логи
- Примитивные логи действий — таблица `logs`.

## Миграции
- Применить: `docker-compose exec backend bash -lc "alembic upgrade head"`
- Создать новую: `docker-compose exec backend bash -lc "alembic revision -m 'msg' --autogenerate"`

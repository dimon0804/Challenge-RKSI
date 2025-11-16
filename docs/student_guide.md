# Гид участника

## Цель
Пройти 3 уровня заданий (легкий, средний, продвинутый), используя API.

## Шаги
- Найдите бот по QR и нажмите Start.
- Зарегистрируйтесь: задайте логин и пароль.
- Получите API-ключ.
- Делайте запросы к API с заголовком `X-API-Key`.

## Примеры

Получить информацию о конкурсе:
```bash
curl -s http://localhost:8000/challenge/info | jq
```

Получить API-ключ:
```bash
curl -s -X POST http://localhost:8000/challenge/auth \
  -H 'Content-Type: application/json' \
  -d '{"username":"vasya","password":"secret123"}'
```

Получить задание этапа:
```bash
curl -s http://localhost:8000/challenge/endpoint/1 \
  -H 'X-API-Key: <ваш_ключ>' | jq
```

Отправить ответ:
```bash
curl -s -X POST http://localhost:8000/challenge/submit \
  -H 'X-API-Key: <ваш_ключ>' -H 'Content-Type: application/json' \
  -d '{"endpoint_id":1,"value":"codeword"}'
```

Python (requests):
```python
import requests
base = "http://localhost:8000"
api_key = "<ваш_ключ>"

r = requests.get(f"{base}/challenge/endpoint/1", headers={"X-API-Key": api_key})
print(r.json())

r = requests.post(f"{base}/challenge/submit", json={"endpoint_id":1, "value":"codeword"}, headers={"X-API-Key": api_key})
print(r.json())
```

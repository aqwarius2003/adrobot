# Keitaro Campaign Manager

Веб-приложение — надёжная обёртка над Keitaro Admin API для управления кампаниями:
- **Campaign Creator**: создание кампании с доменом, группой, источником и двумя потоками (GEO → Google redirect, остальные → Offer 100%).
- **Campaign Editor**: загрузка кампании (`Fetch Streams`), редактирование привязок офферов через локальный черновик (`Draft`), перераспределение весов со строгой суммой 100%, фиксация долей (`Pins`), локальный архив (`Removed` с 0% и `Bring back`), отмена (`Cancel`) и явная публикация (`Push to KT`) с read-back верификацией и защитой от конфликтов.

---

## Технологический стек

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.0 (declarative), Pydantic v2, Alembic, HTTPX.
- **База данных**: SQLite (с persistent volume в Docker и WAL-режимом для надёжной конкурентности).
- **Frontend**: HTML5 / CSS3 / JavaScript (чистый CSS по дизайн-системе `DESIGN.md`, адаптивный интерфейс без лишних зависимостей).
- **Инфраструктура**: Docker (multi-stage build, непривилегированный пользователь `appuser`), Docker Compose, GitHub Actions (CI/CD с публикацией образов в GHCR).

---

## Быстрый старт

### 1. Локальный запуск (Python)

1. Клонируйте репозиторий и создайте виртуальное окружение:
   ```bash
   git clone https://github.com/aqwarius2003/adrobot.git
   cd adrobot
   python -m venv .venv
   source .venv/bin/activate  # На Windows: .venv\Scripts\activate
   ```

2. Установите зависимости:
   ```bash
   pip install -r requirements-dev.txt
   ```

3. Настройте файл окружения:
   ```bash
   cp .env.example .env
   # Укажите реальные KEITARO_BASE_URL и KEITARO_API_KEY в .env
   ```

4. Примените миграции базы данных:
   ```bash
   alembic upgrade head
   ```

5. Запустите сервер:
   ```bash
   uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
   ```
   Приложение доступно по адресу: [http://localhost:8000](http://localhost:8000).
   Диагностика здоровья: [http://localhost:8000/api/health](http://localhost:8000/api/health).

---

### 2. Запуск в Docker

Сборка и запуск через Docker Compose с персистентным хранилищем данных:
```bash
docker compose up -d --build
```
Проверка состояния контейнера:
```bash
docker compose ps
curl http://localhost:8000/api/health
```

---

## Тестирование и линтинг

- **Unit & API тесты**:
  ```bash
  pytest -v
  ```
- **Проверка линтером (Ruff)**:
  ```bash
  ruff check .
  ```

---

## Переменные окружения (.env)

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `KEITARO_BASE_URL` | Базовый URL Keitaro (без `/admin_api/v1`) | `https://tracker.example` |
| `KEITARO_API_KEY` | Ключ доступа Keitaro Admin API | *пусто* |
| `CAMPAIGN_SURNAME` | Фамилия для обязательного префикса `test_<фамилия>_` | `novikov` |
| `DEFAULT_DOMAIN_ID` | Опциональный ID домена по умолчанию | *пусто* |
| `DEFAULT_GROUP_ID` | Опциональный ID группы кампаний | *пусто* |
| `DEFAULT_TRAFFIC_SOURCE_ID` | Опциональный ID источника трафика | *пусто* |
| `DATABASE_URL` | URL подключения SQLAlchemy | `sqlite:///./data/app.db` |
| `APP_ENV` | Окружение (`development` / `staging` / `production`) | `production` |
| `COMMIT_SHA` | SHA текущей версии приложения | `dev` |

> [!IMPORTANT]
> Ключ Keitaro API никогда не передаётся во frontend и не сохраняется в слоях Docker-образа.

---

## Архитектура и CI/CD

- **CI (`.github/workflows/ci.yml`)**: Запускается на PR и push в `main`. Проверяет Ruff, накат миграций на чистую БД, unit/API тесты, сборку Docker и smoke-проверку контейнера `/api/health`.
- **CD (`.github/workflows/cd.yml`)**: Публикует версионированный образ в GitHub Packages (GHCR) по неизменяемому SHA/digest и разворачивает в staging с сохранением состояния SQLite на volume.

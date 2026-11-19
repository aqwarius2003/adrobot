# Keitaro Campaign Manager (Creator & Editor)

Полнофункциональное веб-приложение — надёжная обёртка над Keitaro Admin API для создания и редактирования рекламных кампаний:
- **Campaign Creator (Часть 1)**: создание кампании с доменом, группой, источником и двумя потоками:
  - **Flow 1**: фильтр стран (`Country: accept [GEO]`) → HTTP redirect на `https://google.com/`.
  - **Flow 2**: `Default` fallback для остальных посетителей → выбранный оффер со 100% долей.
- **Campaign Editor (Часть 2)**:
  - **Fetch Streams**: импорт потоков с merge по remote ID, сохранение локального архива, отображение потоков без офферов строкой (opaque stream).
  - **Draft & Персистентность**: локальное добавление (`Add`), удаление в архив (`Remove` с 0%), возврат (`Bring back`) и фиксация долей (`Pins`) без запросов на запись в Keitaro. Черновик переживает перезагрузку браузера и рестарт backend.
  - **Алгоритм распределения весов**: строгое поддержание суммы 100% среди активных офферов, детерминированное распределение остатка `divmod(100 - S_pinned, len(U))`, целые проценты, фиксация pinned-весов других офферов, запрет удаления последнего активного оффера.
  - **Cancel**: мгновенный откат черновика к последнему подтверждённому состоянию Keitaro (Baseline).
  - **Push to KT**: публикация только активных офферов выбранного потока, preflight-сверка fingerprint с удалённым состоянием трекера, post-push GET read-back верификация, reconciliation при timeout.
  - **Внешние удаления**: оффер, удалённый в Keitaro извне, при следующем Fetch переводится в статус `Removed` с 0% и возможностью возврата (`Bring back`).

---

## Архитектура и принципы ООП

Проект построен по принципам чистой слоистой архитектуры и SOLID:
- **API Routes & DTOs (`src/api/`, `src/domain/schemas.py`)**: тонкие контроллеры FastAPI, валидация через Pydantic v2, строгая типизация ответов.
- **Доменные сервисы (`src/services/`)**:
  - `CampaignCreatorService`: сценарий создания кампании, валидация ISO-стран, префикса `test_<фамилия>_`, генерация tracking URL и верификация потоков.
  - `StreamEditorService`: управление черновиком (Draft), merge при Fetch, локальные операции Add/Remove/Bring back/Pin/Cancel.
  - `StreamSyncService`: управление публикацией в Keitaro (Push), preflight-проверка от перезаписи чужих правок, read-back верификация и обработка timeout (reconciliation).
- **Domain Logic & Weights (`src/domain/weights.py`)**: чистый доменный модуль пересчёта долей с неизменяемостью pinned-офферов и строгими инвариантами 100%.
- **Keitaro Gateway Protocol (`src/services/keitaro_client.py`)**: типизированный Protocol интерфейс и реализация на `httpx.AsyncClient` с маскированием ключей в логах, таймаутами и обработкой сетевых ошибок.
- **Data Persistence (`src/domain/models.py`, `src/core/database.py`)**: SQLAlchemy 2.0 Declarative модели, миграции Alembic, WAL-режим SQLite.
- **Dependency Injection (`src/api/dependencies.py`)**: передача зависимостей через механизм FastAPI `Depends()`.

---

## Структура проекта

```text
├── .github/
│   └── workflows/
│       ├── ci.yml            # CI: Ruff lint, Alembic check, Pytest, Docker build & smoke
│       └── cd.yml            # CD: сборка версионированного GHCR образа, deploy staging
├── alembic/                  # Миграции схемы БД
├── data/                     # Персистентное хранилище SQLite (монтируется в Docker volume)
├── src/
│   ├── api/                  # FastAPI роуты (health, campaigns, streams) и DI
│   ├── core/                 # Конфигурация Pydantic, БД, маскирующий логгер
│   ├── domain/               # Модели SQLAlchemy, DTO Pydantic, алгоритм весов
│   ├── services/             # CreatorService, EditorService, SyncService, KeitaroClient
│   ├── web/                  # HTML-шаблоны Jinja2 и статические файлы (CSS, JS)
│   └── main.py               # Точка входа приложения
├── tests/                    # Полный набор тестов (Creator, Editor, Weights, Health, UI)
├── Dockerfile                # Multi-stage сборка с непривилегированным пользователем
├── docker-compose.yml        # Контейнерный запуск с постоянным volume
├── requirements.txt          # Production зависимости
└── requirements-dev.txt      # Тестовые и lint зависимости
```

---

## Быстрый старт

### 1. Локальный запуск (Python)

1. Клонируйте репозиторий:
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

- Интерфейс Creator: [http://localhost:8000/creator](http://localhost:8000/creator)
- Интерфейс Editor: [http://localhost:8000/editor](http://localhost:8000/editor)
- Health check: [http://localhost:8000/api/health](http://localhost:8000/api/health)
- Swagger API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

### 2. Запуск в Docker

Сборка и запуск через Docker Compose:
```bash
docker compose up -d --build
```
Проверка состояния контейнера:
```bash
docker compose ps
curl http://localhost:8000/api/health
```

---

## Тестирование и верификация

Запуск полного тестового набора:
```bash
pytest -v
```

Проверка соответствия стандартам форматирования и линтинга:
```bash
ruff check .
ruff format --check .
```

Проверенные тестовые сценарии:
1. `tests/test_weights.py`:
   - Деление долей для 1, 2, 3, 4 офферов.
   - Фиксация pin при Add/Remove/Bring back (пример 25% + 38% + 37%).
   - Отклонение суммы pins > 100%.
   - Отклонение недостаточной суммы pins при отсутствии unpinned.
   - Нулевая доля active оффера при сумме pins 100%.
   - Запрет удаления последнего active оффера.
2. `tests/test_creator.py`:
   - Создание кампании с префиксом `test_<фамилия>_`.
   - Flow 1: Country filter accept → Google redirect.
   - Flow 2: Default fallback → выбранный оффер 100%.
   - Валидация ISO кодов и автодополнения оффера.
3. `tests/test_editor.py`:
   - Сквозной сценарий из демонстрационного видео.
   - Fetch Streams (opaque поток + offers поток).
   - Add → 25/25/25/25 → dirty.
   - Cancel → восстановление baseline → clean.
   - Push to KT → preflight → read-back верификация → clean.
   - Remove → локальный архив (0%) → Push (отсутствие в payload KT).
   - Bring back → восстановление привязки → Push.
   - Внешнее удаление оффера в Keitaro + Fetch → перевод в Removed (0%).
   - Защита от потери черновика при dirty Fetch (409 Conflict).
   - Защита от устаревших правок (expected_revision 409).
4. `tests/test_health.py` и `tests/test_ui.py`:
   - Диагностика `/api/health` без утечки секретов.
   - Рендеринг экранов Creator и Editor по `DESIGN.md`.

---

## Конфигурация (.env)

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
> Ключ Keitaro API никогда не передаётся во frontend и не сохраняется в слоях Docker-образа. Браузер взаимодействует исключительно со своим FastAPI backend.

---

## Известные ограничения интеграции

1. **Preflight GET → PUT гонка**: Если Keitaro API не поддерживает условные заголовки `If-Match` / ETag, между preflight чтением и записью PUT теоретически возможно изменение объекта сторонним пользователем. Система проводит обязательный post-push GET read-back для обнаружения расхождений.
2. **Параметры поиска офферов**: Метод `GET /offers` в Swagger Keitaro не декларирует query-параметры поиска/пагинации. Поиск офферов в приложении выполняется через внутреннюю фильтрацию списка и прямой поиск по ID.

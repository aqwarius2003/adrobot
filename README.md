# Keitaro Campaign Manager (Creator & Editor)

Полнофункциональное веб-приложение — надёжная обёртка над Keitaro Admin API для создания и редактирования рекламных кампаний:
- **Campaign Creator (Часть 1)**:
  - Создание кампании с доменом, группой, источником и двумя потоками:
    - **Flow 1**: фильтр стран (`Country: accept [GEO]`) → HTTP redirect на `https://google.com/`.
    - **Flow 2**: `Default` fallback для остальных посетителей → выбранный оффер со 100% долей.
  - Обязательный префикс `test_<фамилия>_`, валидация ISO 3166-1 alpha-2, формирование Tracking URL с доменом и параметрами источника.
  - Пошаговая фиксация ID объектов и глубокая read-back верификация маршрутизации.
- **Campaign Editor (Часть 2)**:
  - **Fetch Streams**: импорт потоков с merge по remote ID, сохранение локального архива, отображение потоков без офферов строкой (opaque stream). При внешнем удалении всех офферов поток сохраняется как управляемый, а офферы переводятся в `Removed` с 0% и возможностью `Bring back`.
  - **Draft & Персистентность**: локальное добавление (`Add`), удаление в архив (`Remove` с 0%), возврат (`Bring back`) и фиксация долей (`Pins`) без запросов на запись в Keitaro. Черновик переживает перезагрузку браузера и рестарт backend. Блокировка конфликтующих мутаций во время сетевых операций (`publishing`, `unknown`).
  - **Алгоритм распределения весов**: строгое поддержание суммы 100% среди активных офферов, детерминированное распределение остатка `divmod(100 - S_pinned, len(U))`, целые проценты, фиксация pinned-весов других офферов, запрет удаления последнего активного оффера.
  - **Cancel**: мгновенный откат черновика к последнему подтверждённому состоянию Keitaro (Baseline).
  - **Push to KT**: публикация только активных офферов выбранного потока (без removed и pins), preflight-сверка fingerprint с удалённым состоянием трекера, post-push GET read-back верификация, pin-only оптимизация (пропуск лишнего PUT), reconciliation при timeout.

---

## Технологический стек

- **Язык & Менеджер пакетов**: Python 3.11, [uv](https://docs.astral.sh/uv/) (быстрый менеджер пакетов и виртуальных окружений, lockfile `uv.lock`).
- **Backend**: FastAPI, SQLAlchemy 2.0 (declarative), Pydantic v2, Alembic, HTTPX.
- **База данных**: SQLite (с persistent volume в Docker и WAL-режимом для надёжной конкурентности).
- **Frontend**: HTML5 / CSS3 / JavaScript (чистый CSS по дизайн-системе `DESIGN.md`, адаптивный интерфейс без сторонних JS-библиотек).
- **Инфраструктура**: Docker (multi-stage build с `ghcr.io/astral-sh/uv`, непривилегированный пользователь `appuser`), Docker Compose, GitHub Actions (CI с `setup-uv` и CD для сборки GHCR-образов).

---

## Быстрый старт с `uv`

### 1. Локальный запуск

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/aqwarius2003/adrobot.git
   cd adrobot
   ```

2. Установите зависимости и синхронизируйте окружение по `uv.lock`:
   ```bash
   uv sync --locked --group dev
   ```

3. Настройте файл окружения:
   ```bash
   cp .env.example .env
   # Укажите реальные KEITARO_BASE_URL и KEITARO_API_KEY в .env
   ```

4. Примените миграции базы данных:
   ```bash
   uv run --locked alembic upgrade head
   ```

5. Запустите сервер разработки:
   ```bash
   uv run --locked uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
   ```

- Интерфейс Creator: [http://localhost:8000/creator](http://localhost:8000/creator)
- Интерфейс Editor: [http://localhost:8000/editor](http://localhost:8000/editor)
- Health check: [http://localhost:8000/api/health](http://localhost:8000/api/health)
- Swagger API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

### 2. Запуск в Docker

Сборка и запуск через Docker Compose с персистентным томом для SQLite:
```bash
docker compose up -d --build
```

Проверка состояния контейнера:
```bash
docker compose ps
curl http://localhost:8000/api/health
```

---

## Режимы работы и способы запуска

> «Приложение поддерживает настоящее подключение к Keitaro и автономный деморежим. В общей тестовой установке доступен ограниченный набор офферов, поэтому сценарии распределения весов, pins, архива и синхронизации воспроизводятся на симуляторе с пятью синтетическими офферами. Оба подключения используют общий backend и HTTP-клиент; проверка на симуляторе дополняет проверку настоящего API. Статус реальной интеграции указан отдельно».

### Политика доступности экранов и переход между режимами

| Подключение (`KT_MODE`) | Доступные экраны | Публикация | Точка входа `/` | Переход в шапке |
|---|---|---|---|---|
| **Реальный Keitaro (`real`)** | Редактор существующих кампаний (`/editor`) | Push to KT | `/editor` | «Открыть демо →» (по `DEMO_APP_URL`) |
| **Демо-симулятор (`demo`)** | Создание (`/creator`) и Редактор (`/editor`) | Push to Demo | `/creator` | «Вернуться к Keitaro →» (по `REAL_APP_URL`) |

- **Изоляция баз данных**: В демо-режиме автоматически используется изолированная база данных (`data/demo_app.db` вместо `data/app.db`), исключая пересечение черновиков и seed-данных.
- **Сохранность симулятора**: Состояние симулятора сохраняется в `data/simulator_state.json` и переживает перезапуски процессов до явного вызова сброса (Reset).
- **Потоки без офферов**: Потоки без управляемых офферов (например, Flow 1 со страновым редиректом) отображаются компактной строкой с нейтральным бейджем `Нет редактируемых офферов` без лишних карточек и кнопок.
- **Поиск в Editor**: Добавление офферов в Editor оснащено автодополнением по ID и названию из каталога.

---

### 1. Автономный демо-режим (`KT_MODE=demo`)

Используется для демонстрации Creator и Editor, сценариев распределения весов (34/33/33 → 25/25/25/25 → 20/20/20/20/20), pins, архива и синхронизации без доступа к реальному Keitaro.
В `.env`:
```ini
KT_MODE=demo
DEMO_SIMULATOR_URL=http://127.0.0.1:8001
REAL_APP_URL=http://localhost:8000
```

#### Способ А: В один терминал (локальный автозапуск)
```bash
uv run --locked uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```
*Бэкенд на старте автоматически проверяет порт 8001 и, если симулятор ещё не поднят, запускает его в фоновом потоке на `127.0.0.1:8001`. Дополнительных окон открывать не требуется.*

#### Способ Б: Два одновременных экземпляра (Real на 8000 + Demo на 8002)
- **Терминал 1 (симулятор Keitaro Admin API)**:
  ```bash
  uv run python -m src.simulator.run
  ```
- **Терминал 2 (рабочий экземпляр real)**:
  ```bash
  KT_MODE=real DEMO_APP_URL=http://localhost:8002 uv run --locked uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
  ```
- **Терминал 3 (демонстрационный экземпляр demo)**:
  ```bash
  KT_MODE=demo REAL_APP_URL=http://localhost:8000 uv run --locked uvicorn src.main:app --host 0.0.0.0 --port 8002 --reload
  ```

#### Способ В: В Docker Compose одной командой
```bash
docker compose -f docker-compose.demo.yml up --build
```
*Запускает два изолированных контейнера: `simulator` (порт 8001) и `web` (порт 8000) с отдельным томом `adrobot_demo_data`.*

---

### 2. Реальное подключение к Keitaro (`KT_MODE=real`)

Используется для работы с боевым трекером по API-контракту.
В `.env`:
```ini
KT_MODE=real
KEITARO_BASE_URL=https://your-tracker-host
KEITARO_API_KEY=your_admin_api_key
DEMO_APP_URL=http://localhost:8002
```

- **Локальный запуск**:
  ```bash
  uv run --locked uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
  ```
- **Запуск в Docker Compose**:
  ```bash
  docker compose up -d --build
  ```

---

### 3. Сценарии проверки симулятора в интерфейсе

В режиме `KT_MODE=demo` в Editor доступен сворачиваемый блок **«Сценарии проверки симулятора»**:
- 🔄 **«Сброс демо (Reset)»**: согласованный детерминированный сброс симулятора и локальной БД к исходному состоянию (Flow 2: 34/33/33).
- ✂️ **«Удалить привязку оффера ID 105 в симуляторе»**: симуляция внешнего удаления привязки в трекере для проверки обнаружения при Fetch и сохранения в архиве.
- 🚫 **«Скрыть оффер ID 105 в каталоге»**: симуляция исчезновения оффера из каталога для проверки корректного объяснения при Bring back.

---

## Тестирование и линтинг

- **Запуск всех тестов (57 тестов: unit, integration, regressions, simulator, 9-step demo e2e, browser corrections)**:
  ```bash
  uv run --locked pytest -v
  ```
- **Запуск тестов frontend**:
  ```bash
  node tests/test_frontend_geo.js
  ```
- **Проверка линтером (Ruff)**:
  ```bash
  uv run --locked ruff check .
  ```
- **Проверка форматирования**:
  ```bash
  uv run --locked ruff format --check .
  ```

---

## Переменные окружения (.env)

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `KT_MODE` | Режим трекера: `real` (настоящий KT) или `demo` (симулятор) | `real` |
| `DEMO_SIMULATOR_URL` | URL симулятора в демо-режиме | `http://127.0.0.1:8001` |
| `KEITARO_BASE_URL` | Базовый URL Keitaro (для `KT_MODE=real`, без `/admin_api/v1`) | `https://tracker.example` |
| `KEITARO_API_KEY` | Ключ доступа Keitaro Admin API | *пусто* |
| `CAMPAIGN_SURNAME` | Фамилия для обязательного префикса `test_<фамилия>_` | `rykov` |
| `DEFAULT_DOMAIN_ID` | Опциональный ID домена по умолчанию | *пусто* |
| `DEFAULT_GROUP_ID` | Опциональный ID группы кампаний | *пусто* |
| `DEFAULT_TRAFFIC_SOURCE_ID` | Опциональный ID источника трафика | *пусто* |
| `DATABASE_URL` | URL подключения SQLAlchemy (`app.db` для real, `demo_app.db` для demo) | `sqlite:///./data/app.db` |
| `APP_ENV` | Окружение (`development` / `staging` / `production`) | `production` |
| `COMMIT_SHA` | SHA текущей версии приложения | `dev` |

> [!IMPORTANT]
> Ключ Keitaro API никогда не передаётся во frontend и не сохраняется в слоях Docker-образа. Браузер взаимодействует исключительно со своим FastAPI backend.

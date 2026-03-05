# Trader Panel (Coinbase Advanced Trade, Paper-first)

Production-oriented панель и backend для автоторговли SPOT LONG-only по USDC-парам на Coinbase Advanced Trade API.

## Ключевые принципы
- По умолчанию: `PAPER ON`, `LIVE OFF`.
- Live включается только через Settings + явное подтверждение `ENABLE LIVE`.
- Нет хранения raw API-ключей в логах/ответах.
- Акцент на risk management, state machine исполнения, reconciliation и kill-switch.

## Стек
- Backend: FastAPI, SQLAlchemy 2, Alembic, Pydantic v2
- Worker: Celery + Redis
- DB: PostgreSQL 15
- Frontend: Next.js 14, TypeScript, Tailwind, Lightweight Charts
- Realtime: SSE (`/api/realtime/sse`)
- Observability: JSON logs, Prometheus `/metrics`, опционально Sentry

## Быстрый запуск

Требования: Docker + Docker Compose.

1. Скопировать переменные (если нужно):
```bash
cp .env.example .env
```

2. Поднять все сервисы:
```bash
docker compose up --build
```

После старта:
- Backend: [http://localhost:8000](http://localhost:8000)
- Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)
- Frontend: [http://localhost:3000](http://localhost:3000)
- Flower: [http://localhost:5555](http://localhost:5555)

## Deploy на Render.com

В репозитории добавлен blueprint: [render.yaml](/Users/ramisyaparov/Desktop/Project/Trader/render.yaml)

Что поднимается в Render:
- `trader-postgres` (PostgreSQL)
- `trader-redis` (Key Value / Redis)
- `trader-backend` (FastAPI web service)
- `trader-worker` (Celery worker + beat)
- `trader-frontend` (Next.js web service)

Шаги:
1. Запушьте проект в GitHub/GitLab.
2. В Render: `New` -> `Blueprint`.
3. Подключите репозиторий и подтвердите создание сервисов из `render.yaml`.
4. После первого деплоя проверьте:
  - backend health: `https://<backend-service>.onrender.com/`
  - frontend: `https://<frontend-service>.onrender.com`
5. Для LIVE режима вручную задайте secrets в Render environment:
  - `SECRET_ENCRYPTION_KEY`
  - `COINBASE_API_KEY`
  - `COINBASE_API_SECRET`
  - `COINBASE_API_PASSPHRASE` (если используется)

Примечания:
- Render Postgres обычно выдаёт `postgres://...`; в проекте добавлена нормализация под SQLAlchemy.
- Frontend в Render проксирует `/api/*` на backend по внутренней сети (`BACKEND_PROXY_TARGET` из blueprint), поэтому ручная настройка `NEXT_PUBLIC_API_URL` обычно не нужна.
- CORS в blueprint выставлен как `*` (под Bearer auth, без cookie-сессий).
- План Free может “засыпать” web-сервисы; для стабильного бота лучше paid plan.

## Как создать пользователя

Вариант 1 (UI):
- Открыть frontend `http://localhost:3000`
- На экране логина переключиться в `Sign up`
- Создать аккаунт

Вариант 2 (Swagger):
- `POST /api/auth/signup`
- затем `POST /api/auth/login`
- использовать Bearer token в Authorize

Первый зарегистрированный пользователь автоматически получает роль `admin`.

## Paper/Live режим

### Paper (обязательный режим, default)
- Управляется в `Settings` (`paper_enabled=true`)
- Исполнение через paper state machine (`signals -> orders -> fills -> positions -> exits`)
- Комиссии и slippage настраиваются в `fees_json`

### Live (по флагу)
- В `Settings` включить `live_enabled=true`
- Ввести подтверждение `ENABLE LIVE`
- Убедиться, что ключи Coinbase заданы (ENV или encrypted settings)
- При аномалиях reconciliation/data delay активируется kill-switch

## Coinbase ключи и безопасность

Поддержаны два пути:
1. ENV (`COINBASE_API_KEY`, `COINBASE_API_SECRET`)
2. Settings API (с шифрованием в БД, если задан `SECRET_ENCRYPTION_KEY`)

Важно:
- raw ключи не отдаются обратно API.
- в UI сохраняется только hint (последние 4 символа).
- без `SECRET_ENCRYPTION_KEY` backend не позволит сохранять ключи в БД.

## Universe и данные

Input universe:
`DYDX, INJ, ICP, GALA, AXS, TRB, ONDO, IOTA, NOT, FIL, NEO, ENJ, HYPE, STRK, SLP, ONE, MINA, RVN, RUNE`

Реальная торговля:
- Только доступные `*-USDC` инструменты Coinbase
- TOP-5 по 30d quote volume
- Пересчёт weekly (`universe_selector_task`)

Исторический backfill:
- Фоновая задача `backfill_history_task` (каждые 10 минут) догружает историю по 5m/15m/1h.
- По умолчанию:
  - `BACKFILL_5M_DAYS=180` (6 месяцев),
  - `BACKFILL_15M_DAYS=365`,
  - `BACKFILL_1H_DAYS=730` (24 месяца),
  - ограничение нагрузки: `BACKFILL_MAX_SYMBOLS_PER_RUN=3`, `BACKFILL_MAX_CHUNKS_PER_TF=6`.
- Это даёт постепенный backfill на Render без перегруза API.

## Backtest defaults (обязательно)

По умолчанию период backtest:
- rolling window последних 24 месяцев до текущей даты.

Universe selection для backtest:
1. Получаем Coinbase products.
2. Оставляем только `status=online` и `quote=USDC`.
3. Пересекаем с input ticker list пользователя.
4. Ранжируем по ликвидности (24h notional/volume, иначе proxy по свечам).
5. Выбираем TOP-5.
6. Заменяем пары с недостаточной историей (на большую часть 24м) следующими по ликвидности с лучшим coverage, чтобы увеличить общий период данных.
7. Пары с почти нулевой историей исключаются floor по покрытию (`history_min_coverage_ratio`, default в профиле стратегии).

Execution model (default, conservative):
- `taker-only` для всех входов/выходов.
- `entry slippage = +0.10%`.
- `exit slippage = -0.10%`.
- `stop slippage = -0.20%`.
- комиссии/slippage берутся из встроенного профиля выбранной стратегии (`backend/app/strategies/profiles.py`).
- сигнал формируется только на закрытии свечи, вход возможен только со следующей свечи (no lookahead).

Обязательные stress-сценарии:
- `1.5x` от fees+slippage.
- `2.0x` от fees+slippage.

В каждом отчёте backtest (`metrics_json`) явно сохраняются:
- assumptions (fees/slippage/taker-only/period/universe),
- data availability по каждому кандидату,
- base + stress_1_5x + stress_2_0x метрики.

## Стратегии (MVP)

### StrategyBreakoutRetest
- Regime (1H): `close > EMA200`, `EMA200 slope >= 0`, `ATR% < threshold`
- Signal (5m): breakout above highest high (lookback=20) на закрытой свече
- Entry: limit near retest (`breakout_level - k*ATR`)
- SL: `entry - 1*ATR`
- TP: partial 1R + финальный 2R / trailing stop

### StrategyPullbackToTrend
- Тот же 1H regime
- Pullback к EMA50 + RSI filter
- Вход при reclaim выше EMA20
- SL: ниже pullback low
- TP: фиксированный R-multiple

### MeanReversionHardStop
- Работает только внутри 1H regime filter (`close > EMA200`, `slope >= 0`, `ATR% < threshold`)
- Setup (5m): закрытие ниже нижней Bollinger Bands(20,2) **или** `RSI(14) < 30`
- Safety guard: сигнал игнорируется, если 5m close ниже `EMA200_5m`
- Trigger (5m): следующая закрытая свеча возвращается выше `BB_low` **или** RSI пересекает вверх уровень 30
- Entry: цена trigger-close (исполнение в paper/backtest с консервативной моделью комиссии/slippage)
- SL: `min(low за последние 15 свечей) - 0.2*ATR(14)` (fallback: `-0.1%`, если ATR недоступен)
- Ограничение риска: если `(entry - stop)/entry > mr_max_stop_pct`, сигнал пропускается
- TP: `entry + mr_tp_rr * (entry - stop)` (по умолчанию `1.2R`)
- Без трейлинга, без partial, без DCA.

No DCA / no martingale.

Параметры `MeanReversionHardStop` (встроенный профиль стратегии):
- `mr_bb_period` (default `20`)
- `mr_bb_std` (default `2`)
- `mr_rsi_period` (default `14`)
- `mr_rsi_entry_threshold` (default `30`)
- `mr_safety_ema_period` (default `200`)
- `mr_lookback_stop` (default `15`)
- `mr_stop_atr_buffer` (default `0.2`)
- `mr_max_stop_pct` (default `0.03`)
- `mr_tp_rr` (default `1.2`)

## Риск-менеджмент (default)
- Используются per-strategy профили из `backend/app/strategies/profiles.py`:
  - `risk` (лимиты/позиционирование),
  - `fees` (maker/taker/slippage),
  - `signal` (параметры генерации),
  - `backtest` (coverage/input tickers).
- Настройки из UI `Settings -> Risk params / Strategy params / Fees` больше не являются источником исполнения для стратегий.

Position sizing:
```text
size_quote = equity * risk_per_trade_pct/100 / (entry - stop) * entry
```
С учётом `min_size` и `size_increment` инструмента.

Для SPOT-профиля включён дополнительный cap:
- `max_position_notional_pct` (default `100`) ограничивает quote-ношинал позиции долей от equity.
- Это не даёт paper/live открывать позицию больше доступного капитала.

Фильтр минимального edge:
- `min_profit_to_cost_ratio` (default `1.2`) блокирует входы, где ожидаемый профит по TP
  слишком мал относительно издержек (maker+taker+market slippage).
- При срабатывании сигнал помечается `cancelled` с причиной в `meta_json.edge_check`.

## SSE события
Endpoint: `GET /api/realtime/sse`

Типы событий:
- `signal_created`
- `order_placed`
- `order_filled`
- `position_opened`
- `position_closed`
- `kill_switch`
- `data_delay`
- `error`

Admin-only logs:
- `GET /api/system/logs?limit=200`

## Структура репозитория

```text
backend/
  app/
    api core db models schemas services strategies risk execution realtime workers
  alembic/
  tests/
frontend/
  app/
  components/
  lib/
```

## Полезные команды

Backend tests:
```bash
cd backend
python3 -m pytest
```

Frontend build:
```bash
cd frontend
npm install
npm run build
```

## Troubleshooting

1. `docker: command not found`
- Установить Docker Desktop и убедиться, что CLI доступен в PATH.

2. `Live mode requires Coinbase API credentials`
- Добавить ключи через ENV или Settings (при `SECRET_ENCRYPTION_KEY`).

3. `SECRET_ENCRYPTION_KEY not set`
- Либо задайте `SECRET_ENCRYPTION_KEY`, либо используйте только ENV-ключи.

4. Нет сигналов/сделок
- Проверьте что:
  - есть данные свечей (ingest worker)
  - universe заполнен (`/api/universe/current`)
  - `paper_enabled=true`
  - kill-switch не активирован

5. Kill-switch сработал из-за data delay
- Проверьте доступ к Coinbase API, Redis, и работу `ingest_market_data_task`.

6. Local Python < 3.11
- Проект рассчитан на Python 3.11+ (в Docker уже используется 3.11).

## Примечание по LIVE исполнению

Live execution изолирован флагом и по умолчанию выключен. Перед боевым включением необходимо:
- проверить формат/permissions ключей Coinbase,
- протестировать end-to-end на paper,
- проверить reconciliation и отмену открытых entry-ордеров при аномалиях.

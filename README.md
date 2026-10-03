# Titan API

Backend for **Titan**, a Persian gaming platform: a digital-goods store, esports tournaments, teams and a
player dashboard. It serves the Next.js frontend in [`titan-front`](../titan-front) and replaces all of its
mock data.

**Stack:** Python 3.13 · Django 5.2 · Django REST Framework · PostgreSQL · SimpleJWT · drf-spectacular

---

## Quick start

### Option A: local (SQLite, fastest)

```bash
uv sync                                   # install deps (incl. dev tools) into .venv
cp .env.example .env                      # then set SECRET_KEY and FIELD_ENCRYPTION_KEY (see below)
uv run python manage.py migrate
uv run python manage.py seed              # demo data + images copied from ../titan-front/public
uv run python manage.py runserver
```

Generate the two required secrets:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(50))"                               # SECRET_KEY
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FIELD_ENCRYPTION_KEY
```

Comment out `DATABASE_URL` in `.env` to use SQLite.

### Option B: Docker (PostgreSQL)

```bash
cp .env.example .env                      # set the secrets as above
docker compose up -d                      # postgres + api (runs migrations on start)
docker compose exec api python manage.py seed
```

### Useful URLs

| URL | What |
|---|---|
| http://localhost:8000/api/docs/ | Swagger UI (interactive API docs) |
| http://localhost:8000/api/redoc/ | ReDoc |
| http://localhost:8000/api/schema/ | OpenAPI 3 schema (for client generation) |
| http://localhost:8000/admin/ | Staff back office |
| http://localhost:8000/api/health/ | Liveness / readiness probe |

### Demo accounts (after `seed`)

| Who | Login |
|---|---|
| Demo player **طاها / TahaTitan** | phone `09123456789`, code `12345` (any phone number works with `12345` while `OTP_TEST_CODE` is set) |
| Admin | phone `09000000000`, password `admin` (or `$SEED_ADMIN_PASSWORD`) at `/admin/` |

The demo player mirrors the dashboard mock:
- a wallet of ۱,۴۵۰,۰۰۰ تومان
- two saved game accounts
- a completed and a processing order
- captain of **Iran Titans**, which is in a live semi-final in `valorant-season-cup`
- a pending team invitation from Shadow Strike
- a pending friend request, and friends with presence

`seed` is idempotent, so you can run it any time. `seed --flush` wipes the database first (DEBUG only).

---

## Development

```bash
uv run pytest                     # 285 tests
uv run pytest --cov               # with coverage (~96%)
uv run ruff check . && uv run ruff format .
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py spectacular --validate --file schema.yml
pre-commit install                # ruff + migration check on every commit
```

Tests run against in-memory SQLite. To run them against PostgreSQL, set `TEST_DATABASE_URL`.

After changing user-facing strings, update translations:

```bash
uv run python manage.py makemessages -l fa --ignore=.venv
# edit locale/fa/LC_MESSAGES/django.po
uv run python manage.py compilemessages -l fa --ignore=.venv
```

---

## Architecture

```
config/            settings (base/dev/test/prod), root URLs
apps/
  core/            shared building blocks: base model, encrypted field, error handling, pagination,
                   home/search/dashboard aggregators, `seed` command
  accounts/        users (mobile-number based), OTP login, game accounts, friends, presence, ranks, badges
  catalog/         games, platforms, categories, products and variants, reviews, wishlist
  orders/          server-side cart, checkout, orders
  payments/        wallet and ledger, payments, gateways (ZarinPal, Fake), purpose-handler registry
  teams/           teams, memberships, invitations, invite links
  tournaments/     seasons, tournaments, registration, bracket engine, results, stats, leaderboards
  notifications/   per-user notifications
  content/         promos/banners, announcements, contact channels, site settings
```

### Layering

| Layer | Responsibility |
|---|---|
| `views.py` | HTTP only: parse input, call one service or selector, serialize the result |
| `serializers.py` | Validation and representation. No business rules. |
| `services.py` | Business rules and writes, in `transaction.atomic`, with `select_for_update` for money and capacity |
| `selectors.py` | Read queries with the needed `select_related`, `prefetch_related` and annotations |

Business rules raise typed `DomainError` subclasses, for example `InsufficientBalance` or `TournamentFull`.
These are turned into API responses in one place, `apps/core/exceptions.py`.

The payments app knows nothing about orders or tournaments. Each app that sells something registers a
`PurposeHandler` (`on_paid` / `on_failed`) from its `AppConfig.ready()`. Wallet payments and gateway
callbacks then run the same fulfilment code.

### API conventions

- **Base path:** `/api/v1/`.
- **JSON is camelCase** both ways (`prizePool`, `isWishlisted`), matching the frontend's TypeScript types.
  Query parameters stay snake_case (`?price_max=…&page_size=…`).
- **Auth:** send `Authorization: Bearer <access>`. Access tokens last 15 minutes and refresh tokens 30 days.
  Refresh tokens rotate, and the old one is blacklisted.
- **Money:** integers in **Toman**. Tournament prize pools carry their own `prizeCurrency`, either `IRT` or `USD`.
- **Dates:** ISO-8601 in `Asia/Tehran`. The frontend formats them as Jalali.
- **Language:** messages are Persian by default. Send `Accept-Language: en` for English.
- **Pagination:** `?page=2&page_size=24` returns `{count, next, previous, results}`.
- **Errors:** always `{detail, code, errors?}`.
  - `code` is stable, so branch on it, e.g. `insufficient_balance`, `tournament_full`, `variant_required`.
  - `errors` holds per-field validation messages.
- **Lookups:** games, products and tournaments by `slug`; players by `username`; orders by `number`;
  teams by `id`.

### Products: fixed price vs. options

Every product is one of two shapes, and `hasVariants` tells you which.

| | Fixed price (e.g. Fortnite Crew Pack) | With options (e.g. V-Bucks 1,000 / 2,800 / 5,000) |
|---|---|---|
| `hasVariants` | `false` | `true` |
| `price` / `originalPrice` | the price | the **cheapest** option's price, for "از …" (from) labels |
| `priceMax` | `null` | the most expensive option, for "X تا Y" (X to Y) ranges |
| `variants` (detail) | `[]` | each option: `{id, label, price, originalPrice, discountPercent, inStock}` |
| Add to cart | `{product}` | `{product, variant}`; leaving out `variant` fails with `variant_required` |
| Stock | `stock` on the product | per option; `inStock` on the product means "any option available" |

For option products, the product-level `price`, `originalPrice`, `priceMax` and `stock` are a cache. It is
recalculated automatically whenever an option is created, edited, deleted or sold. In the admin you either
type a fixed price or add options. A product with neither is rejected.

Price sorting and the `price_min`/`price_max` filters use the starting price.

### Key flows

**Login (OTP):**
1. `POST auth/otp/request {phone}` sends an SMS. Requests are throttled per phone and per IP.
2. `POST auth/otp/verify {phone, code}` returns `{access, refresh, isNewUser, user}`. The number is
   registered on its first login.

**Checkout:**
1. `POST orders/checkout {paymentMethod, gameAccount?}`.
   - **wallet:** the order is paid immediately.
   - **gateway:** the response has a `paymentUrl` to redirect to. ZarinPal returns to
     `/api/v1/payments/callback/`, which verifies the payment and redirects the browser to
     `FRONTEND_URL/payment/result?status=…&paymentId=…&purpose=…&reference=…`.
2. Products with `deliveryType: "account"` require a saved game account.
3. Stock is re-checked when payment is confirmed. If it ran out, the amount goes back to the wallet.

**Tournaments:**
1. Registration (`POST tournaments/{slug}/register`):
   - **Solo:** any user.
   - **Team:** only the captain, the team must play the tournament's game, have at least `teamSize`
     members, and none of its players can be in another entry.
   - **Fees:** paid by wallet or gateway. Withdrawing before the start refunds to the wallet.
2. Staff generate the bracket. Entrants are seeded by points, with byes for a non-power-of-two field.
3. Staff report scores. Winners advance automatically. When the final ends:
   - placements are computed
   - prizes are paid: points to everyone placed, wallet credit only for IRT prizes; tied places
     split the prize
   - champions get a badge
   - per-season player and team stats update the leaderboards

**Presence:** the client calls `POST me/heartbeat {status: online|away|in_game, game?}` about every 60
seconds. Users go `offline` after 2 minutes of silence.

---

## Endpoint map

| Area | Endpoints |
|---|---|
| Auth | `POST auth/otp/request/` · `POST auth/otp/verify/` · `POST auth/token/refresh/` · `POST auth/logout/` |
| Me | `GET/PATCH me/` · `POST me/avatar/` · `POST me/phone/change/request/` · `POST me/phone/change/verify/` · `POST me/heartbeat/` · `GET me/dashboard/` · `GET me/stats/` |
| Game accounts | `GET/POST me/game-accounts/` · `GET/PATCH/DELETE me/game-accounts/{id}/`. The password is write-only. |
| Friends | `GET/POST me/friends/` · `DELETE me/friends/{username}/` · `GET me/friend-requests/` · `POST me/friend-requests/{id}/accept/` · `…/decline/` |
| Players | `GET players/{username}/` · `GET ranks/` |
| Catalog | `GET games/` · `GET games/{slug}/` · `GET platforms/` · `GET product-categories/` · `GET products/` · `GET products/{slug}/` · `GET products/{slug}/related/` |
| Reviews | `GET/POST products/{slug}/reviews/` · `DELETE products/{slug}/reviews/mine/` |
| Wishlist | `GET/POST me/wishlist/` · `DELETE me/wishlist/{slug}/` |
| Cart | `GET/DELETE cart/` · `POST cart/items/` · `PATCH/DELETE cart/items/{id}/` · `POST cart/merge/` |
| Orders | `POST orders/checkout/` · `GET me/orders/` · `GET me/orders/{number}/` · `POST me/orders/{number}/cancel/` |
| Wallet & payments | `GET wallet/` · `GET wallet/transactions/` · `POST wallet/topup/` · `GET payments/{id}/` · `GET payments/callback/` (gateway only) |
| Teams | `GET/POST teams/` · `GET/PATCH/DELETE teams/{id}/` · `GET teams/{id}/members/` · `DELETE teams/{id}/members/{userId}/` · `POST teams/{id}/members/{userId}/promote/` · `POST teams/{id}/invite-code/regenerate/` · `GET/POST teams/{id}/invitations/` · `DELETE teams/{id}/invitations/{id}/` · `POST teams/join/` · `GET teams/{id}/tournaments/` |
| My teams | `GET me/teams/` (with `activity` for the rail) · `GET me/team-invitations/` · `POST me/team-invitations/{id}/accept/` · `…/decline/` |
| Tournaments | `GET tournaments/` · `GET tournaments/{slug}/` · `GET …/participants/` · `GET …/bracket/` · `GET …/eligible-teams/` · `POST/DELETE …/register/` · `POST …/generate-bracket/` (staff) |
| Matches | `PATCH matches/{id}/` (staff) · `GET matches/upcoming/` · `GET me/tournaments/` · `GET seasons/` |
| Leaderboards | `GET leaderboards/players/?game=&season=` · `GET leaderboards/teams/?game=&season=` |
| Notifications | `GET notifications/?unread=1` · `GET notifications/unread-count/` · `POST notifications/{id}/read/` · `POST notifications/read-all/` · `DELETE notifications/{id}/` |
| Content | `GET content/promos/?placement=` · `GET content/announcements/` · `GET content/contact/` |
| Aggregates | `GET home/` · `GET search/?q=` |

Useful filters:
- `products/?game=fortnite&category=gift-card&platform=pc&price_min=&price_max=&on_sale=true&search=`
  - Sort with `ordering=-popularity` (default), `price`, `-price` or `-created_at`.
- `tournaments/?game=&status=registration_open&participant_type=team&free=true&search=`
  - Sort with `ordering=starts_at` (default) or `-prize_pool`.

---

## Frontend integration map

| Page (`titan-front`) | Mock it replaces | Endpoints |
|---|---|---|
| Layout: top bar | `CATALOG` search, notification dot, cart badge | `search/?q=`, `notifications/unread-count/`, `cart/` |
| Layout: right rail | hard-coded teams/friends with status | `me/teams/` (`activity`), `me/friends/` (`presence`), `me/heartbeat/` |
| `/` home | `data/games.ts`, `data/tournaments.ts`, `HRS` stats, announcement toasts | `home/` |
| `/store` | `PRODUCTS`, `DISCOUNT_PROMOS`, `BESTSELLER_PROMOS`, `TABS` | `products/`, `content/promos/?placement=store_discount` and `?placement=store_bestseller`, `games/?is_featured=true` |
| `/product/[id]` | hard-coded V-Bucks page and recommendations | `products/{slug}/`, `products/{slug}/related/`, `products/{slug}/reviews/`, `me/wishlist/` |
| `/cart` | `AppContext` cart | `cart/`, `cart/items/…`. On login, `cart/merge/` the guest cart. |
| `/checkout` | mocked saved accounts and ZarrinPal alert | `me/game-accounts/`, `wallet/`, `orders/checkout/`; then redirect to `paymentUrl` |
| `/tournament` | ranks, upcoming matches, leaderboards, `ScoreWidget` | `ranks/`, `tournaments/?status=registration_open`, `matches/upcoming/`, `leaderboards/*`, `me/stats/`, `content/promos/?placement=tournament_hero` |
| `/tournaments` | `ALL_TOURNAMENTS` with filters | `tournaments/` with `game`, `status`, `search` and `ordering` |
| `/tournaments/[id]` | details, participants, prizes, team picker | `tournaments/{slug}/`, `…/participants/`, `…/bracket/`, `…/eligible-teams/`, `…/register/` |
| `/dashboard?tab=overview` | wallet, counts, history, last orders | `me/dashboard/` |
| `?tab=profile` | form defaults | `me/`, `me/avatar/`, `me/phone/change/*` |
| `?tab=accounts` | `accounts` state | `me/game-accounts/` |
| `?tab=orders` | ORD-12345, ORD-12344 | `me/orders/` |
| `?tab=favorites` | empty state | `me/wishlist/` |
| `?tab=teams` | Iran Titans, Sniper Elite | `me/teams/` |
| `?tab=tournaments` | two hard-coded entries | `me/tournaments/` (`currentStage`) |
| `?tab=notifications` | invitation and registration notices | `notifications/`, `me/team-invitations/{id}/accept\|decline/`, `notifications/read-all/` |
| `/teams/create` | static form | `POST teams/` (returns `inviteUrl`) |
| `/teams/[id]` | Iran Titans roster | `teams/{id}/`, `teams/{id}/tournaments/` |
| `/teams/[id]/manage` | settings, members, invite link, dissolve | `PATCH/DELETE teams/{id}/`, `…/members/{userId}/`, `…/promote/`, `…/invite-code/regenerate/`, `…/invitations/` |
| `/tournaments/[id]/bracket` | hard-coded QF/SF/final | `tournaments/{slug}/bracket/` (`isMine`, `lobbyCode`) |
| `/contact` | static cards and online badge | `content/contact/` |

### Frontend integration status

`titan-front` is fully wired to this API: routes use slugs, there is an OTP login page and a
`/payment/result` page, presence heartbeats are sent, and the guest cart merges into the server cart on login.
See `titan-front/README.md` for how the client is organised.

---

## Production

- **Settings:** `config.settings.prod` is the default for `wsgi.py` and the Docker image.
  - Required env vars: `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `ALLOWED_HOSTS`, `DATABASE_URL`,
    `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS`, `ZARINPAL_MERCHANT_ID`, `PAYMENT_CALLBACK_URL`.
  - Also set `SMS_BACKEND=apps.accounts.sms.KavenegarSMSBackend` and `KAVENEGAR_API_KEY`.
  - Set `ZARINPAL_SANDBOX=False`.
- **Image:** `docker build -t titan-api .` builds a multi-stage, non-root image. It runs gunicorn,
  serves static files through WhiteNoise, and has a healthcheck on `/api/health/`.
- **Deploy steps:** run `python manage.py migrate` on each release.
- **Media:** serve `/media/` from a reverse proxy or object storage. Django does not serve media when
  `DEBUG` is off.
- **`FIELD_ENCRYPTION_KEY`:** never rotate it without re-encrypting the data. Game-account passwords and
  delivered redeem codes are Fernet-encrypted with it.

### Staff back office (`/admin/`)

The admin uses the [django-unfold](https://unfoldadmin.com) theme in Titan colours, in English (LTR), with
light and dark modes. The public API stays Persian; `AdminEnglishMiddleware` switches only `/admin/`.

- **Where things live:**
  - Theme settings, palette and sidebar: `config/settings/unfold.py`.
  - Dashboard data and sidebar badges: `apps/core/admin_site.py`.
  - Dashboard template: `templates/admin/index.html`.
  - Styles and logos: `apps/core/static/admin/titan/`.
  - Shared helpers, plus the Group and JWT token admins: `apps/core/admin.py`.
- **Dashboard:**
  - KPIs: 30-day revenue and new players, each compared with the previous 30 days; orders to fulfil; live
    tournaments.
  - A 14-day revenue chart and orders by status.
  - Latest orders, upcoming tournaments with fill rate, top products and low stock.
- **Sidebar:** grouped navigation (Sales, Store, Esports, Community, Content, Security). It shows live badges
  for orders to fulfil, live matches and hidden reviews, and hides items the staff member can't view.
  <kbd>Ctrl</kbd>+<kbd>K</kbd> searches across models.
- **Orders:**
  - Enter redeem codes on each order line, then use **"Mark as delivered"**. It is in the order's top bar,
    or a bulk action in the list. The customer sees the codes and is notified.
  - **"Start processing"** / **"Mark as processing"** moves paid orders to processing.
- **Products:**
  - Tabbed form (Pricing & stock, Delivery, Content, Merchandising), with options and gallery as tabs.
  - **"View on storefront"** opens the product on the frontend.
  - Bulk actions: activate, deactivate and toggle the best-seller badge.
- **Reviews:** approve or hide in bulk, or with the per-row toggle. Product ratings are recalculated
  automatically.
- **Game accounts:** passwords are hidden unless the staff member has the `accounts.reveal_password`
  permission.
- **Tournaments:**
  - **"Generate bracket & start"** builds the bracket. It is available on the tournament page or as a bulk
    action. Registrations and matches are shown as tabs.
  - Enter both scores on a match to report the result. Advancement, stats and prizes follow automatically.
- **Wallets:**
  - Balances are read-only.
  - **"Adjust balance"** on a wallet opens a dialog that credits or debits through the ledgered wallet
    services.
  - The full ledger is under *Wallet ledger*.
- **Notifications:** **"Broadcast to all players"** sends a system notification to every active player.

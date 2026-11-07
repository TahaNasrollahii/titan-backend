# Titan Backend (`titan-back`) Rules

## Mandatory Website ↔ Telegram Bot Synchronization Rule
1. Any catalog, guide, or feature change made to `titan-front` must also be reflected in `titan-bot` and `titan-back` (including catalog migrations/seeds and `apps/telegram` when applicable).
2. Always work on and push to the **`vercel-deploy`** branch (`origin/vercel-deploy`).

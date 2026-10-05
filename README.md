# Blackberry Telegram Bot — Vercel Webhook

## Features
- Vercel webhook mode (no `run_polling()` in deployment)
- Reply to a user's message with `/approve` or `/free` to permanently exempt that user from this protection in the database.
- `/unapprove` re-enables protection.
- 3 messages in 3 seconds => recent spam burst is deleted.
- After spam deletion, admins get APPROVE/FREE, BAN and UNAPPROVE buttons.
- Admin-only `/block`, `/unblock`, `/mute`, `/unmute`.
- Local Windows: SQLite fallback.
- Vercel: PostgreSQL when `DATABASE_URL` is configured.

## Telegram permissions
Make the bot a group administrator and enable:
- Delete messages
- Ban users
- Restrict members

If Telegram privacy mode prevents normal group messages from reaching the bot, disable privacy mode in BotFather (`/setprivacy`) or make the bot an admin.

## Vercel deployment
1. Put the project in GitHub.
2. Import the repository into Vercel.
3. Add `BOT_TOKEN`, `DATABASE_URL`, and `WEBHOOK_SECRET` in Vercel Environment Variables.
4. Deploy.
5. Set the Telegram webhook to:
   `https://YOUR-PROJECT.vercel.app/api/index`
   with the same `WEBHOOK_SECRET`.

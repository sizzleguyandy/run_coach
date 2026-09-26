# Instructions for the Hermes agent: give the coach profile its own Telegram bot

Your owner has installed the run coach into a separate profile, called
`bob` below (use the real profile name if it's different). Bob needs:
1. the same LLM your owner's default Hermes uses
2. its own Telegram bot, served by the same gateway as the default bot
3. Sheets-only Google access for form intake

Work through the steps in order, from the **default** profile's
terminal. Report back after each step. **Ask your owner before any
step marked ASK.** Never print API keys or bot tokens in chat. Refer
to them by name.

---

## Step 1 — Give bob the default profile's model

Profiles don't share settings: bob has its own `config.yaml` and
`.env`, and it never falls back to the default profile's keys.

1. Read the default profile's model block (the `model:` section of the
   default home's `config.yaml`, e.g. `hermes config show` or open the
   file). Note `provider`, `default` (the model name) and, if present,
   `base_url`.
2. Copy them into bob:
   ```bash
   hermes -p bob config set model.provider <provider>
   hermes -p bob config set model.default <model>
   hermes -p bob config set model.base_url <base_url>     # only if the default has one
   ```
3. Credentials. Work out how the default profile authenticates to
   that provider:
   - **An API key in the default `.env`** (e.g. `OPENROUTER_API_KEY`,
     `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). ASK: *"Bob needs its own
     copy of your <NAME> key to use the same model. OK to copy that one
     line into bob's .env?"* If yes, copy only that line into bob's
     `.env`.
   - **An OAuth login** (Nous Portal, Claude Pro/Max, OpenAI Codex,
     xAI…). Don't copy `auth.json`: some of these refresh tokens are
     single-use, and a copy would sign one of the profiles out.
     Profiles read the login from the root `auth.json`, so there may be
     nothing to do. If the test below fails, run
     `hermes -p bob auth add <provider>` with your owner.
4. Test: `hermes -p bob chat -q "Reply with just OK"` must answer.
5. Tell your owner that bob now uses the same model, but it's a copy:
   if they switch the default profile's model later, they need to run
   step 2 again for bob.

## Step 2 — Create bob's Telegram bot

A Telegram bot token can belong to one profile only. Bob needs a
**new** bot, not the default one.

1. ASK your owner to create it (only they can, from their Telegram
   account):
   - **Easiest:** open the Hermes dashboard, switch to the **bob**
     profile, go to **Messaging → Telegram**, and use **Create with QR**.
     Hermes creates the bot and writes `TELEGRAM_BOT_TOKEN` and
     `TELEGRAM_ALLOWED_USERS` into bob's `.env` itself. Then skip to
     item 3.
   - **Manual:** in Telegram, message **@BotFather** and send
     `/newbot`. Pick a name (e.g. "Run Coach") and a username ending
     in `bot`. BotFather replies with a token.
2. If manual: put these two lines in bob's `.env` (not the default
   `.env`):
   ```
   TELEGRAM_BOT_TOKEN=<the new token>
   TELEGRAM_ALLOWED_USERS=<owner's Telegram user id>
   ```
   The owner's id is the number in the default profile's
   `TELEGRAM_ALLOWED_USERS`, or ask them to message @userinfobot. This
   makes the owner bob's admin. **Don't add athletes here**; they
   join by pairing (step 5).
3. Check the tokens differ: bob's `TELEGRAM_BOT_TOKEN` must not equal
   the default profile's. Compare them without printing either.

## Step 3 — Serve bob from the same gateway

Hermes multiplexes by default: the default profile's gateway process
serves every profile's bots.

```bash
hermes gateway restart          # the default (host) gateway re-scans profiles
hermes gateway status           # bob should be listed as served, with telegram connected
```
- Don't run `hermes -p bob gateway install` or `start`. Named profiles
  are served by the host gateway, and those commands refuse without
  `--force`, which would start a second gateway.
- If `status` shows a boxed warning saying bob isn't served, follow
  the fix it names. The usual cause is a duplicate bot token (step 2,
  item 3). Tell your owner what it said.

Test: ASK your owner to send the **new** bot a message in Telegram
(e.g. "hello"). It should reply in the coach's voice. Their normal bot
should carry on as before.

## Step 4 — Google Sheets for form intake (Sheets only)

Don't copy the default profile's Google token into bob. That token
carries every scope the default profile was granted (possibly Gmail,
Drive, Calendar), and bob talks to people you don't know. Give bob its
own login, limited to Sheets.

The Google skill saves its login in whichever profile runs it, so this
has to happen inside bob:
1. ASK your owner to open a bob session (`hermes -p bob chat`, or
   message the new bot as the owner) and send:
   `/google-workspace set up Google access for Sheets only (--services sheets)`
2. Bob walks them through the Google sign-in in their browser.
   Afterwards, in that same bob session, the owner can test it:
   *"read the first 3 rows of Sheet <SHEET_ID>"* (the form's
   response Sheet).

## Step 5 — How athletes get in (tell your owner)

Hermes turns away Telegram users who aren't on the allowlist. Athletes
join by **pairing**:
1. The athlete fills in the Google Form, then messages bob's bot.
2. The bot replies with a pairing code (valid for 1 hour).
3. The athlete sends that code to the owner (text, WhatsApp, anything).
4. The owner approves it:
   `hermes -p bob pairing approve telegram <CODE>`

Also useful: `hermes -p bob pairing list` (pending and approved) and
`hermes -p bob pairing revoke telegram <user id>`.

Suggest adding this to the Google Form's confirmation message:
> "Next, message the coach bot at t.me/<bot username>. It will reply
> with a pairing code. Send that code to your coach so they can let
> you in, and the bot will then send you your plan."

Athletes are regular users, not admins. They can chat with the coach
but can't run slash commands. Only the owner is admin.

## Step 6 — Hand over

Tell your owner:
> "Bob is on its own Telegram bot, using the same model as me, served
> by the same gateway. Open a chat with the new bot and type
> `/run-coach set up the coaching system`. It will set up the hourly
> form-intake job and the daily backup."

## Rules

- **Change bob's files only.** Never change the default profile's
  `config.yaml`, `.env` or bot.
- **Keep secrets out of chat.** Never show tokens or keys in chat or
  logs.
- **Stop on failure.** If a step fails and the fix isn't obvious, stop
  and show your owner the exact error.

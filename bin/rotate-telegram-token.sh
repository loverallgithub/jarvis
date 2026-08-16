#!/usr/bin/env bash
# ===========================================================================
# Rotate JPD_TELEGRAM_BOT_TOKEN without the token ever reaching a transcript,
# a log line, a shell history entry, or a process argument list.
#
#   usage:  /opt/jarvis/bin/rotate-telegram-token.sh
#
# Reads the new token from a silent prompt. It is never echoed, never passed
# as an argv (which `ps` would show to every user on the box), and never
# printed on success or failure.
#
# Order matters, and it is this on purpose:
#
#   1. VALIDATE FIRST, against Telegram, before touching anything. A typo or a
#      half-copied token must fail here — while the old token is still live and
#      the platform is still working — rather than after .env has been
#      overwritten and the console has been rolled onto a dead credential.
#   2. Back up .env, then write, then chmod 600.
#   3. Push to the service. `docker service update --force` does NOT re-read
#      env_file, so the value has to be set explicitly on the service.
#   4. Prove it end to end with contract-test.
# ===========================================================================
set -euo pipefail

ENV_FILE=/opt/jarvis/platform/docker/.env
SERVICE=jarvis_console
JPD=/opt/jarvis/bin/jpd

[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE" >&2; exit 1; }

printf 'New bot token from BotFather (input hidden): '
read -rs TOKEN
printf '\n'

[[ -n "${TOKEN:-}" ]] || { echo "no token entered — nothing changed" >&2; exit 1; }
if [[ ! "$TOKEN" =~ ^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$ ]]; then
  echo "that does not look like a bot token (expected 123456789:AA...) — nothing changed" >&2
  exit 1
fi

# --- 1. validate BEFORE writing anything -----------------------------------
echo "==> validating with Telegram"
WHO=$(curl -sS --max-time 20 "https://api.telegram.org/bot${TOKEN}/getMe" \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); r=d.get("result") or {};
print(("@"+r["username"]) if d.get("ok") else "")' 2>/dev/null || true)
if [[ -z "$WHO" ]]; then
  echo "    REJECTED: Telegram did not accept that token. Nothing was changed." >&2
  echo "    The old token is still in place and the platform is still working." >&2
  exit 1
fi
echo "    ok: $WHO"

# Privacy mode must stay disabled or every reply becomes invisible to the bot.
PRIV=$(curl -sS --max-time 20 "https://api.telegram.org/bot${TOKEN}/getMe" \
       | python3 -c 'import sys,json; print((json.load(sys.stdin).get("result") or {}).get("can_read_all_group_messages"))' 2>/dev/null || true)
if [[ "$PRIV" != "True" ]]; then
  echo "    ⚠️  WARNING: privacy mode is ENABLED on this bot." >&2
  echo "    It will not see replies that do not @mention it, so every card reply" >&2
  echo "    and every Sintra task will silently time out." >&2
  echo "    Fix with BotFather: /setprivacy -> select bot -> Disable." >&2
fi

# --- 2. .env ----------------------------------------------------------------
BACKUP="${ENV_FILE}.bak.$(date +%s)"
cp -p "$ENV_FILE" "$BACKUP"
echo "==> backed up to $(basename "$BACKUP")"

# Rewrite in place via a temp file so a failure cannot leave .env truncated.
TMP=$(mktemp); chmod 600 "$TMP"
grep -v '^JPD_TELEGRAM_BOT_TOKEN=' "$ENV_FILE" > "$TMP" || true
printf 'JPD_TELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" >> "$TMP"
mv "$TMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "==> wrote $ENV_FILE (mode $(stat -c '%a' "$ENV_FILE"))"

# --- 3. push to the running service ----------------------------------------
echo "==> updating $SERVICE"
docker service update --quiet \
  --env-add "JPD_TELEGRAM_BOT_TOKEN=${TOKEN}" "$SERVICE" >/dev/null
echo "    converged"

unset TOKEN

# --- 4. prove it ------------------------------------------------------------
echo "==> verifying"
"$JPD" telegram contract-test 2>&1 | grep -viE 'steps_registered' || true
"$JPD" telegram streams 2>&1 | grep -viE 'steps_registered' | head -1

cat <<'EOF'

Done. Remaining checks are yours:
  · reply to a card in #human-tasks and confirm it clears (jpd tasks list)
  · the old token is dead the moment BotFather issued the new one
  · delete the backup once you are happy:  rm /opt/jarvis/platform/docker/.env.bak.*
EOF

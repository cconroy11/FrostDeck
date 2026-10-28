#!/bin/bash
# FrostDeck game launcher. Runs inside Steam's Proton session for a game
# shortcut, replacing the plain `proton waitforexitandrun Battle.net.exe` step.
#
#   bnet-launch.sh <proton> <Battle.net.exe> <launch code> <battle.net log dir> <helper log>
#
# The current Battle.net client ignores `--exec="launch X"` on a cold start but
# honours it when a second copy forwards it to an already running instance over
# IPC. So:
#
#   1. Start the client with `--autostarted`, which (with its "launch minimized"
#      setting on) keeps its window hidden, so Steam's own loading screen stays
#      up until the game appears.
#   2. Wait until the client's log says it is fully up, then run a second copy
#      with the exec argument inside the same Wine session.
#   3. Once the client logs that the game process started, close the client so
#      only the game is left. Steam keeps tracking the session until the game
#      exits, because the game is a descendant of Steam's reaper.
set -u
proton="$1"
exe="$2"
code="$3"
logdir="$4"
helperlog="$5"

log() {
  printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" >>"$helperlog" 2>/dev/null
}

newest_log() {
  ls -t "$logdir"/battle.net-*.log 2>/dev/null | head -n 1
}

# PIDs of Windows-side processes whose command line matches a regex. Wine
# exposes Windows command lines in /proc, so matching a backslash path such as
# "\Battle.net\Battle.net.exe" selects the real client and nothing else: the
# Linux-side processes that merely mention the path (this script, Proton, the
# steam.exe shim) use forward slashes. This script and the Proton process it
# started are excluded explicitly as well.
find_pids() {
  local regex="$1" file pid
  # One grep over every command line (NUL-separated records, so each argument
  # is its own line) instead of two processes per /proc entry, which took
  # minutes on the Deck.
  grep -l -z -E -- "$regex" /proc/[0-9]*/cmdline 2>/dev/null | tr '\0' '\n' | while IFS= read -r file; do
    pid="${file#/proc/}"
    pid="${pid%/cmdline}"
    [ "$pid" = "$$" ] || [ "$pid" = "${main:-0}" ] && continue
    echo "$pid"
  done
}

close_matching() {
  local label="$1" regex="$2" pids
  pids=$(find_pids "$regex" | tr '\n' ' ')
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
  fi
  log "closed $label: ${pids:-nothing running}"
}

finish() {
  wait "$main" 2>/dev/null
  log "session ended"
  exit 0
}

: >"$helperlog" 2>/dev/null   # one launch per log; keeps it small and unambiguous
log "start code=$code"
before="$(newest_log)"

"$proton" waitforexitandrun "$exe" --autostarted &
main=$!
log "client started hidden, pid=$main"

send_exec() {
  log "sending --exec=launch $code ($1)"
  "$proton" run "$exe" "--exec=launch $code" &
}

# Wait for the client to create its log and open its IPC channel, then send the
# launch command straight away. The client queues commands that arrive before
# sign-in completes, so the launch fires the moment it is able to, ahead of the
# main window being presented.
client_log=""
for _ in $(seq 1 120); do
  sleep 1
  if ! kill -0 "$main" 2>/dev/null; then
    log "client exited before it was ready"
    finish
  fi
  current="$(newest_log)"
  if [ -n "$current" ] && [ "$current" != "$before" ] \
    && grep -q "Opening IPC shared memory" "$current"; then
    client_log="$current"
    break
  fi
done
if [ -z "$client_log" ]; then
  log "timed out waiting for the client; leaving it running"
  finish
fi
# Battle.net discards commands that arrive before sign-in completes rather than
# queuing them, and there is no log line that marks the exact moment it becomes
# able to launch. So send the command every two seconds until the client logs
# that it accepted one, then stop so nothing is sent twice.
accepted() { grep -q -E "LaunchBinary|Launched .* with args" "$client_log" 2>/dev/null; }
sends=0
for i in $(seq 0 90); do
  accepted && break
  if [ $((i % 2)) -eq 0 ]; then
    sends=$((sends + 1))
    send_exec "attempt $sends"
  fi
  sleep 1
done

# The client logs the game process it spawned, e.g.
#   Launched C:/Program Files (x86)/Hearthstone/Hearthstone.exe with args: ...
game_exe=""
for _ in $(seq 1 60); do
  sleep 1
  line=$(grep -m1 -E "Launched .* with args" "$client_log" 2>/dev/null || true)
  if [ -n "$line" ]; then
    game_exe=$(sed -E 's/.*Launched (.*) with args.*/\1/' <<<"$line")
    break
  fi
done
if [ -z "$game_exe" ]; then
  log "client never confirmed a game launch; leaving it running"
  finish
fi
game_name="${game_exe##*/}"
game_regex="[\\\\/]$(printf '%s' "$game_name" | sed 's/[.[\\*^$]/\\&/g')( |$)"
client_regex='\\Battle\.net\\Battle\.net\.exe'
agent_regex='^[A-Za-z]:.*[\\/]Agent\.exe'
log "game launched: $game_name"

# Give the game a moment to put up its window, so Steam goes straight from its
# loading screen to the game, then close the client (what Battle.net's own
# "exit on game launch" setting does).
for _ in $(seq 1 30); do
  sleep 1
  [ -n "$(find_pids "$game_regex")" ] && break
done
sleep 15   # let the game finish its sign-in handshake with the client first
close_matching "Battle.net client" "$client_regex"

# Stay alive while the game runs, then clear Blizzard's update agent so the
# session ends promptly once the game is gone.
while [ -n "$(find_pids "$game_regex")" ]; do
  sleep 3
done
log "game exited"
close_matching "Blizzard agent" "$agent_regex"
finish

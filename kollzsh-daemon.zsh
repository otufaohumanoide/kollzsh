#!/usr/bin/env zsh

KOLLZSH_LOG_FILE="/tmp/kollzsh_debug.log"
touch "$KOLLZSH_LOG_FILE"
chmod 666 "$KOLLZSH_LOG_FILE"

log_debug() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    {
        echo "[${timestamp}] $1"
        if [ -n "$2" ]; then
            echo "Data: $2"
            echo "----------------------------------------"
        fi
    } >> "$KOLLZSH_LOG_FILE" 2>&1
}

ensure_daemon_running() {
    local restart=0

    if [ -f /tmp/kollzshd.pid ]; then
        local pid=$(cat /tmp/kollzshd.pid)
        if kill -0 "$pid" 2>/dev/null; then
            if [ -n "$(find "${KOLLZSH_PLUGIN_DIR}" -maxdepth 1 -name '*.py' -newer /tmp/kollzshd.pid)" ]; then
                log_debug "Daemon code changed, restarting"
                kill "$pid" 2>/dev/null
                rm -f /tmp/kollzshd.pid /tmp/kollzshd.sock
                restart=1
            else
                return 0
            fi
        fi
    fi

    rm -f /tmp/kollzshd.sock
    python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd.py" &
    disown
    local waited=0
    while [ ! -S "$KOLLZSH_DAEMON_SOCK" ] && [ $waited -lt 50 ]; do
        sleep 0.1
        waited=$((waited + 1))
    done
    if [ $waited -ge 50 ]; then
        log_debug "Daemon failed to start within 5 seconds"
    fi
}

_kollzsh_cleanup() {
    if [ -f /tmp/kollzshd.pid ]; then
        local pid=$(cat /tmp/kollzshd.pid)
        kill "$pid" 2>/dev/null
        rm -f /tmp/kollzshd.pid
        rm -f /tmp/kollzshd.sock
    fi
}

trap _kollzsh_cleanup EXIT

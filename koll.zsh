# default shortcut as Ctrl-o
(( ! ${+KOLLZSH_HOTKEY} )) && typeset -g KOLLZSH_HOTKEY='^o'
# default llm model
(( ! ${+KOLLZSH_MODEL} )) && typeset -g KOLLZSH_MODEL='unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL'
# default response number as 5
(( ! ${+KOLLZSH_COMMAND_COUNT} )) && typeset -g KOLLZSH_COMMAND_COUNT='5'
# default llm server host
(( ! ${+KOLLZSH_URL} )) && typeset -g KOLLZSH_URL='http://localhost:8080'
# daemon socket path
(( ! ${+KOLLZSH_DAEMON_SOCK} )) && typeset -g KOLLZSH_DAEMON_SOCK='/tmp/kollzshd.sock'
# Plugin directory (auto-detectado: diretorio deste script)
(( ! ${+KOLLZSH_PLUGIN_DIR} )) && typeset -g KOLLZSH_PLUGIN_DIR="${${(%):-%x}:A:h}"

# Source utility functions
source "${KOLLZSH_PLUGIN_DIR}/utils.zsh"

# Set up logging with proper permissions
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

validate_required() {
  # Check required tools are installed
  check_command "fzf" || return 1
  check_command "python3" || return 1
  
  # Check if LLM server is running
  check_llm_running || return 1
  
  # Check if the specified model exists
  if ! curl -s "${KOLLZSH_URL}/v1/models" | grep -q "$KOLLZSH_MODEL"; then
    echo "🚨 Model ${KOLLZSH_MODEL} not found!"
    echo "Please ensure it is available on the LLM server at ${KOLLZSH_URL}"
    return 1
  fi
}

ensure_daemon_running() {
  local restart=0

  if [ -f /tmp/kollzshd.pid ]; then
    local pid=$(cat /tmp/kollzshd.pid)
    if kill -0 "$pid" 2>/dev/null; then
      # Check if daemon code is newer than PID file (code changed)
      if [ "${KOLLZSH_PLUGIN_DIR}/kollzshd.py" -nt /tmp/kollzshd.pid ] || \
         [ "${KOLLZSH_PLUGIN_DIR}/kollzshd_pi.py" -nt /tmp/kollzshd.pid ] || \
         [ "${KOLLZSH_PLUGIN_DIR}/kollzshd_commands.py" -nt /tmp/kollzshd.pid ] || \
         [ "${KOLLZSH_PLUGIN_DIR}/kollzshd_llm.py" -nt /tmp/kollzshd.pid ] || \
         [ "${KOLLZSH_PLUGIN_DIR}/kollzshd_logging.py" -nt /tmp/kollzshd.pid ]; then
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

send_to_daemon() {
  local query="$1"
  local mode="${2:-navigation}"
  python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" send \
    --query "$query" --mode "$mode" --lines
}

fzf_kollzsh() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"

  zle -I
  echo -n "👻 Please wait..."

  ensure_daemon_running
  local result
  result=$(send_to_daemon "$user_query" "navigation")

  if [ -n "$result" ]; then
    result=$(echo "$result" | FZF_DEFAULT_OPTS="--reverse --cycle" fzf)
  fi

  if [ -n "$result" ]; then
    BUFFER="$result"
    CURSOR=${#BUFFER}
  fi

  zle reset-prompt
}

stream_from_daemon() {
  local query="$1"
  python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" stream --query "$query"
}

fzf_kollzsh_deep() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"
  zle -I

  ensure_daemon_running

  local response
  response=$(stream_from_daemon "$user_query")

  if [ -z "$response" ]; then
    zle reset-prompt
    return
  fi

  local lines
  lines=$(echo "$response" | python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" parse-lines)

  if [ -n "$lines" ]; then
    BUFFER="$lines"
    CURSOR=${#BUFFER}
  fi

  zle reset-prompt
}

# Clean up daemon on ZSH exit
_kollzsh_cleanup() {
  if [ -f /tmp/kollzshd.pid ]; then
    local pid=$(cat /tmp/kollzshd.pid)
    kill "$pid" 2>/dev/null
    rm -f /tmp/kollzshd.pid
    rm -f /tmp/kollzshd.sock
  fi
}
trap _kollzsh_cleanup EXIT

validate_required

autoload -U fzf_kollzsh
zle -N fzf_kollzsh
bindkey "$KOLLZSH_HOTKEY" fzf_kollzsh

autoload -U fzf_kollzsh_deep
zle -N fzf_kollzsh_deep
bindkey "^f" fzf_kollzsh_deep

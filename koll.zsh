# default shortcut as Ctrl-o
(( ! ${+KOLLZSH_HOTKEY} )) && typeset -g KOLLZSH_HOTKEY='^o'
# default llm model
(( ! ${+KOLLZSH_MODEL} )) && typeset -g KOLLZSH_MODEL='unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL'
# default response number as 5
(( ! ${+KOLLZSH_COMMAND_COUNT} )) && typeset -g KOLLZSH_COMMAND_COUNT='5'
# default llm server host
(( ! ${+KOLLZSH_URL} )) && typeset -g KOLLZSH_URL='http://localhost:8080'
# daemon socket path
(( ! ${+KOLLZSH_DAEMON_SOCK} )) && typeset -g KOLLZSH_DAEMON_SOCK='/tmp/kollzshd.sock'

# Source utility functions
source "${0:A:h}/utils.zsh"

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
  if [ -f /tmp/kollzshd.pid ]; then
    local pid=$(cat /tmp/kollzshd.pid)
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  local plugin_dir="${KOLLZSH_PLUGIN_DIR:-${${(%):-%x}:A:h}}"
  python3 "${plugin_dir}/kollzshd.py" &
  disown
  local waited=0
  while [ ! -S "$KOLLZSH_DAEMON_SOCK" ] && [ $waited -lt 50 ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
}

send_to_daemon() {
  local query="$1"
  local mode="${2:-navigation}"
  python3 -c "
import socket, json, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sys.argv[1])
payload = json.dumps({'query': sys.argv[2], 'mode': sys.argv[3]})
s.sendall(payload.encode() + b'\n')
s.shutdown(socket.SHUT_WR)
data = b''
while True:
    chunk = s.recv(4096)
    if not chunk:
        break
    data += chunk
s.close()
print(data.decode().strip())
" "$KOLLZSH_DAEMON_SOCK" "$query" "$mode"
}

fzf_kollzsh() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"

  # Invalidate zle display so fzf can take over the terminal
  zle -I
  echo -n "👻 Please wait..."

  log_debug "Sending query:" "$user_query"

  ensure_daemon_running
  local response
  response=$(send_to_daemon "$user_query" "navigation")

  local result
  if [ -n "$response" ]; then
    local lines
    lines=$(echo "$response" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read())
    for line in data.get("lines", []):
        print(line)
except:
    pass
')
    if [ -n "$lines" ]; then
      result=$(echo "$lines" | FZF_DEFAULT_OPTS="--reverse --cycle" fzf)
    fi
  fi

  if [ -n "$result" ]; then
    BUFFER="$result"
    CURSOR=${#BUFFER}
    log_debug "Selected command:" "$result"
  else
    log_debug "No command selected"
  fi

  zle reset-prompt
}

fzf_kollzsh_deep() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"

  zle -I
  echo "🔍 Buscando e analisando..."

  ensure_daemon_running

  local response
  response=$(send_to_daemon "$user_query" "deep")

  if [ -z "$response" ]; then
    log_debug "No response from daemon"
    zle reset-prompt
    return
  fi

  local lines
  lines=$(echo "$response" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read())
    if "cwd" in data:
        print("---")
    for line in data.get("lines", []):
        print(line)
except:
    pass
')

  if [ -n "$lines" ]; then
    BUFFER="$lines"
    CURSOR=${#BUFFER}
    log_debug "Deep result:" "$lines"
  else
    log_debug "No results from deep search"
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

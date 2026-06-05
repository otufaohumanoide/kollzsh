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
  local plugin_dir="${KOLLZSH_PLUGIN_DIR:-${${(%):-%x}:A:h}}"
  local restart=0

  if [ -f /tmp/kollzshd.pid ]; then
    local pid=$(cat /tmp/kollzshd.pid)
    if kill -0 "$pid" 2>/dev/null; then
      # Check if daemon code is newer than PID file (code changed)
      if [ "${plugin_dir}/kollzshd.py" -nt /tmp/kollzshd.pid ] || \
         [ "${plugin_dir}/kollzshd_pi.py" -nt /tmp/kollzshd.pid ]; then
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
  python3 "${plugin_dir}/kollzshd.py" &
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

stream_from_daemon() {
  local query="$1"
  python3 -c '
import json, socket, sys

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sys.argv[1])
payload = json.dumps({"query": sys.argv[2], "mode": "deep"})
s.sendall(payload.encode() + b"\n")
s.shutdown(socket.SHUT_WR)

def render(event):
    t = event.get("type", "")
    r = event.get("round", "")
    out = []
    if t == "think":
        if event.get("status") == "start":
            if r:
                out.append("")
                sep = "\u2500" * 38
                out.append("\u2500\u2500 Round " + str(r) + "/2 " + sep)
            out.append("  [THINK]  " + event.get("msg", ""))
    elif t == "cmd":
        out.append("  [CMD]    " + event.get("cmd", ""))
    elif t == "out":
        for line in event.get("lines", []):
            out.append("  [OUT]      " + line)
    elif t == "read":
        out.append("  [READ]   Lendo " + event.get("file", "") + "...")
    elif t == "result":
        for line in event.get("lines", []):
            out.append("  [DONE]   " + line)
    elif t == "error":
        out.append("  [ERRO]   " + event.get("msg", ""))
    return "\n".join(out)

s.settimeout(300.0)
try:
    reader = s.makefile("r")
    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "done":
            print(json.dumps({"lines": event.get("lines", []), "cwd": event.get("cwd", "")}))
            break
        rendered = render(event)
        if rendered:
            print(rendered, file=sys.stderr)
except (BrokenPipeError, OSError) as e:
    print(json.dumps({"lines": ["Connection lost: " + str(e)], "cwd": ""}))
finally:
    try:
        s.close()
    except Exception:
        pass
' "$KOLLZSH_DAEMON_SOCK" "$query"
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
    log_debug "No response from daemon"
    zle reset-prompt
    return
  fi

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

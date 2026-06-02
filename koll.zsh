# default shortcut as Ctrl-o
(( ! ${+KOLLZSH_HOTKEY} )) && typeset -g KOLLZSH_HOTKEY='^o'
# default llm model
(( ! ${+KOLLZSH_MODEL} )) && typeset -g KOLLZSH_MODEL='unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL'
# default response number as 5
(( ! ${+KOLLZSH_COMMAND_COUNT} )) && typeset -g KOLLZSH_COMMAND_COUNT='5'
# default llm server host
(( ! ${+KOLLZSH_URL} )) && typeset -g KOLLZSH_URL='http://localhost:8080'

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
  check_command "jq" || return 1
  check_command "fzf" || return 1
  check_command "curl" || return 1
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

fzf_kollzsh() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"
  local plugin_dir="${KOLLZSH_PLUGIN_DIR:-${${(%):-%x}:A:h}}"
  local result

  # Invalidate zle display so fzf can take over the terminal
  zle -I
  echo -n "👻 Please wait..."

  log_debug "Sending query:" "$user_query"

  result=$(python3 "$plugin_dir/llm_util.py" "$user_query" | FZF_DEFAULT_OPTS="--reverse --cycle" fzf)

  if [ -n "$result" ]; then
    BUFFER="$result"
    CURSOR=${#BUFFER}
    log_debug "Selected command:" "$result"
  else
    log_debug "No command selected"
  fi

  zle reset-prompt
}

validate_required

autoload -U fzf_kollzsh
zle -N fzf_kollzsh
bindkey "$KOLLZSH_HOTKEY" fzf_kollzsh

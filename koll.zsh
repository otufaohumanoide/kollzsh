(( ! ${+KOLLZSH_HOTKEY} )) && typeset -g KOLLZSH_HOTKEY='^o'
(( ! ${+KOLLZSH_MODEL} )) && typeset -g KOLLZSH_MODEL='unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL'
(( ! ${+KOLLZSH_URL} )) && typeset -g KOLLZSH_URL='http://localhost:8080'
(( ! ${+KOLLZSH_DAEMON_SOCK} )) && typeset -g KOLLZSH_DAEMON_SOCK='/tmp/kollzshd.sock'
(( ! ${+KOLLZSH_PLUGIN_DIR} )) && typeset -g KOLLZSH_PLUGIN_DIR="${${(%):-%x}:A:h}"

source "${KOLLZSH_PLUGIN_DIR}/utils.zsh"
source "${KOLLZSH_PLUGIN_DIR}/kollzsh-validate.zsh"
source "${KOLLZSH_PLUGIN_DIR}/kollzsh-daemon.zsh"

send_to_daemon() {
    python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" send \
        --query "$1" --mode "${2:-navigation}" --lines
}

stream_from_daemon() {
    python3 -u "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" stream --query "$1"
}

fzf_kollzsh() {
    setopt extendedglob
    validate_required || return 1
    local user_query="$BUFFER"
    zle -I
    echo -n "👻 Please wait..."
    ensure_daemon_running
    local result=$(send_to_daemon "$user_query" "navigation")
    if [ -n "$result" ]; then
        result=$(echo "$result" | FZF_DEFAULT_OPTS="--reverse --cycle" fzf)
    fi
    if [ -n "$result" ]; then
        BUFFER="$result"
        CURSOR=${#BUFFER}
    fi
    zle reset-prompt
}

fzf_kollzsh_deep() {
    setopt extendedglob
    validate_required || return 1
    local user_query="$BUFFER"
    zle -I
    ensure_daemon_running
    stream_from_daemon "$user_query"
    printf '\n'
    read -k 1 "?Pressione qualquer tecla para continuar... "
    zle reset-prompt
}

validate_required

autoload -U fzf_kollzsh
zle -N fzf_kollzsh
bindkey "$KOLLZSH_HOTKEY" fzf_kollzsh

autoload -U fzf_kollzsh_deep
zle -N fzf_kollzsh_deep
bindkey "^f" fzf_kollzsh_deep

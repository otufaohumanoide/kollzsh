#!/usr/bin/env zsh

check_llm_running() {
    local health_url="${KOLLZSH_URL}/v1/models"
    if ! curl -sf "$health_url" > /dev/null 2>&1; then
        echo "🚨 LLM server not running at ${KOLLZSH_URL}!"
        return 1
    fi
    return 0
}

validate_required() {
    check_command "fzf" || return 1
    check_command "python3" || return 1
    check_llm_running || return 1
    if ! curl -s "${KOLLZSH_URL}/v1/models" | grep -q "$KOLLZSH_MODEL"; then
        echo "🚨 Model ${KOLLZSH_MODEL} not found!"
        echo "Please ensure it is available on the LLM server at ${KOLLZSH_URL}"
        return 1
    fi
}

#!/usr/bin/env zsh

check_llm_running() {
  local url="${KOLLZSH_URL:-http://localhost:8080}"
  local response
  response=$(command curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 "$url/v1/models" 2>/dev/null)
  if [[ "$response" != "200" ]]; then
    case "$response" in
      000)  print -P "  %F{red}[kollzsh]%f LLM server unreachable at $url" >&2 ;;
      401|403) print -P "  %F{red}[kollzsh]%f LLM server at $url returned HTTP $response (unauthorized)" >&2 ;;
      *)    print -P "  %F{red}[kollzsh]%f LLM server at $url returned HTTP $response" >&2 ;;
    esac
    return 1
  fi
  return 0
}

check_daemon_running() {
  if [[ ! -S "${KOLLZSH_DAEMON_SOCK:-/tmp/kollzshd.sock}" ]]; then
    print -P "  %F{red}[kollzsh]%f Daemon socket not found. Start daemon with: kollzshd.py &" >&2
    return 1
  fi
  return 0
}

check_fzf_installed() {
  if ! command -v fzf &>/dev/null; then
    print -P "  %F{red}[kollzsh]%f fzf not found. Install with: sudo apt install fzf (brew install fzf)" >&2
    return 1
  fi
  return 0
}

validate_required() {
  check_llm_running || return 1
  check_daemon_running || return 1
  check_fzf_installed || return 1
}

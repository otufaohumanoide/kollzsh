#!/usr/bin/env zsh

detect_os() {
    case "$(uname -s)" in
        Linux*)     echo "linux";;
        Darwin*)    echo "mac";;
        CYGWIN*)    echo "windows";;
        MINGW*)     echo "windows";;
        *)          echo "unknown";;
    esac
}

get_package_manager_install_cmd() {
    local os=$(detect_os)
    case "$os" in
        "linux")
            if command -v apt-get &> /dev/null; then
                echo "sudo apt-get install -y"
            elif command -v dnf &> /dev/null; then
                echo "sudo dnf install -y"
            elif command -v yum &> /dev/null; then
                echo "sudo yum install -y"
            elif command -v pacman &> /dev/null; then
                echo "sudo pacman -S --noconfirm"
            else
                echo "unknown"
            fi
            ;;
        "mac")
            if command -v brew &> /dev/null; then
                echo "brew install"
            else
                echo "unknown"
            fi
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

check_command() {
    local cmd="$1"
    local package_name="${2:-$1}"
    if ! command -v "$cmd" &> /dev/null; then
        local install_cmd=$(get_package_manager_install_cmd)
        if [ "$install_cmd" = "unknown" ]; then
            echo "🚨 $cmd not found! Please install it manually."
        else
            echo "🚨 $cmd not found! You can install it with: $install_cmd $package_name"
        fi
        return 1
    fi
    return 0
}

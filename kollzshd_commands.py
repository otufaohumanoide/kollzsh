#!/usr/bin/env python3
"""Módulo de validação e execução de comandos para o daemon kollzsh.

Fornece funções para:
- Validar segurança de comandos shell (whitelist read-only vs destrutivos)
- Executar comandos em um subprocesso bash persistente via protocolo de marcadores
- Truncar output longo (sanduíche: top 20 + bottom 20 linhas)
- Parsear e validar listas de comandos vindas de respostas da LLM

O daemon mantém um processo bash persistente. Cada comando é enviado com
marcadores ``__KSEP__`` e ``__KEND__`` para isolar stdout e CWD do resultado.
"""

import ast
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from typing import List, Optional, Tuple

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Comandos de leitura — executam automaticamente sem confirmação do usuário
READONLY_COMMANDS: set[str] = {
    "grep", "rg", "ag", "find", "ls", "cat", "head", "tail", "wc", "stat",
    "file", "sort", "uniq", "diff", "tree", "pwd", "echo", "which", "type",
    "du", "df", "bat", "less", "strings", "nl", "od", "xxd", "column", "cut",
    "tr", "fmt", "fold", "expand", "pr", "printf", "env", "dirname", "basename",
    "realpath", "readlink", "date", "cal", "bc", "seq", "shuf", "tsort",
    "comm", "paste", "join", "look", "split", "cksum", "md5sum", "sha1sum",
    "sha256sum",
}

# Comandos destrutivos — exigem confirmação do usuário via fzf
DESTRUCTIVE_COMMANDS: set[str] = {
    "rm", "mv", "cp", "chmod", "chown", "sudo", "kill", "apt", "pacman",
    "brew", "dnf", "yum", "pip", "npm", "docker", "systemctl", "mkfs", "dd",
    "shutdown", "reboot", "halt", "poweroff", "init",
}


def log_debug(message: str, data: Optional[str] = None) -> None:
    """Registra mensagem de debug no log do daemon.

    Args:
        message: Mensagem principal.
        data: Dados adicionais opcionais (payload, output, etc).
    """
    if data:
        logging.debug(f"{message}\nData: {data}\n----------------------------------------")
    else:
        logging.debug(message)


def is_readonly(command: str) -> bool:
    """Verifica se um comando é seguro para executar sem confirmação.

    Analisa cada token do comando (tratando quotes com shlex) e verifica
    se contém apenas comandos da whitelist. Retorna False se encontrar
    qualquer comando destrutivo ou redirect (``>``, ``>>``).

    Args:
        command: Comando shell a ser verificado.

    Returns:
        True se o comando contém apenas comandos read-only.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Fallback para shlex em input malformado (quotes não fechadas)
        tokens = command.split()
    has_readonly = False
    for t in tokens:
        if t in DESTRUCTIVE_COMMANDS or t in (">", ">>"):
            return False
        if t in READONLY_COMMANDS:
            has_readonly = True
    return has_readonly


def validate_command_safety(command: str) -> Tuple[bool, str]:
    """Validação profunda de segurança de um comando shell.

    Camadas de verificação:
    1. Comando vazio ou heredoc
    2. Primeiro token contra DESTRUCTIVE_COMMANDS
    3. Segmentos de pipeline (``ls | rm -rf /`` é bloqueado)
    4. Padrões perigosos via regex (``rm -rf /``, redirects para ``/dev/sd``)
    5. Flags perigosas específicas por comando

    Args:
        command: Comando shell a ser validado.

    Returns:
        Tupla ``(is_safe, reason)`` — True se seguro, False com motivo da rejeição.
    """
    if not command or not command.strip():
        return False, "Empty command"

    command = command.strip()

    if '<<' in command:
        return False, "Contains heredoc syntax"

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    if not tokens:
        return False, "No command tokens found"

    # Extrai o nome do comando, removendo caminho absoluto se presente
    first_token = tokens[0].lower()
    if '/' in first_token:
        first_token = first_token.split('/')[-1]

    if first_token in DESTRUCTIVE_COMMANDS:
        return False, f"Blocked command: {first_token}"

    # Verifica cada segmento de pipeline individualmente
    if '|' in command:
        segments = command.split('|')
        for segment in segments:
            segment = segment.strip()
            if segment:
                segment_first = segment.split()[0].lower() if segment.split() else ''
                if segment_first in DESTRUCTIVE_COMMANDS:
                    return False, f"Blocked command in pipeline: {segment_first}"

    # Padrões regex para operações perigosas (rm -rf /, redirects para dispositivos)
    dangerous_patterns = [
        r'rm\s+.*-rf\s+/',
        r'rm\s+.*-r\s+/',
        r'>\s*/dev/sd',
        r'\|\s*rm',
        r'\|\s*sudo',
        r'&&\s*rm',
        r';\s*rm',
        r'`rm',
        r'\$\{.*rm',
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Matches dangerous pattern: {pattern}"

    # Padrões de redirect para dispositivos de bloco
    redirect_patterns = [
        r'>\s*/dev/sd',
        r'>>\s*/dev/sd',
        r'2>&1\s*\|\s*rm',
    ]
    for pattern in redirect_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Matches dangerous redirect: {pattern}"

    # Flags perigosas específicas por comando
    dangerous_flags = {
        'rm': ['-rf', '-r', '-f', '-fr'],
        'dd': ['of=', 'if='],
        'mkfs': [],
        'chmod': ['-R', '777', '755'],
        'chown': ['-R'],
    }
    if first_token in dangerous_flags:
        for flag in dangerous_flags[first_token]:
            if flag in command:
                return False, f"Dangerous flag: {flag}"

    return True, "Command is safe"


def truncate_output(lines: List[str], max_lines: int = 120) -> List[str]:
    """Trunca output usando método sanduíche: top 60 + bottom 60.

    Quando o output excede ``max_lines``, mantém as primeiras e últimas
    60 linhas, inserindo um marcador indicando quantas foram omitidas.
    Isso evita que outputs gigantes estourem o contexto da LLM.

    Args:
        lines: Lista de linhas de output.
        max_lines: Limite máximo de linhas (padrão 120).

    Returns:
        Lista truncada com marcador de omission no meio.
    """
    if len(lines) <= max_lines:
        return lines
    top = 60
    bottom = 60
    omitted = len(lines) - top - bottom
    return (lines[:top]
            + [f"... ({omitted} lines omitted) ..."]
            + lines[-bottom:])


def execute_command(
    command: str,
    shell_proc: subprocess.Popen,
    timeout: int = 30
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Executa um comando no shell persistente e captura output + CWD.

    Usa o protocolo de marcadores para isolar o output do comando da
    saída do ``pwd``:

    1. Envia ``{command}; echo '__KSEP__'; pwd; echo '__KEND__'`` no stdin
    2. Lê stdout até ``__KEND__``
    3. Tudo antes de ``__KSEP__`` é output do comando
    4. Tudo depois é o CWD atualizado

    Args:
        command: Comando shell a executar.
        shell_proc: Processo bash persistente (subprocess.Popen).
        timeout: Timeout em segundos (não implementado no protocolo de marcadores).

    Returns:
        Tupla ``(success, output, new_cwd)``:
        - success: True se o comando foi executado com sucesso
        - output: Output truncado do comando (ou mensagem de erro)
        - new_cwd: Diretório de trabalho atualizado (ou None se não mudou)
    """
    is_safe, reason = validate_command_safety(command)
    if not is_safe:
        log_debug(f"Command rejected: {reason}", command)
        return False, f"Command rejected: {reason}", None

    log_debug(f"Executing command: {command}")

    # O marcador __KSEP__ separa output do comando da saída do pwd
    sentinel = f"{command}; echo '__KSEP__'; pwd; echo '__KEND__'"

    try:
        shell_proc.stdin.write(sentinel + '\n')
        shell_proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        log_debug(f"Error writing to shell stdin: {e}")
        return False, f"[LLM GEROU] {command}\n[ERRO] Shell pipe error: {e}", None

    output_lines: List[str] = []
    new_cwd: Optional[str] = None
    found_sep = False

    try:
        while True:
            line = shell_proc.stdout.readline()
            if not line:
                log_debug("Shell stdout closed unexpectedly")
                return False, f"[LLM GEROU] {command}\n[ERRO] Shell process exited", None

            line_stripped = line.rstrip('\n')

            # Fim do output — extrai CWD e retorna
            if line_stripped == '__KEND__':
                break

            # Separador — a partir daqui é a saída do pwd
            if line_stripped == '__KSEP__':
                found_sep = True
                continue

            if not found_sep:
                output_lines.append(line_stripped)
            else:
                new_cwd = line_stripped
    except Exception as e:
        log_debug(f"Error reading shell output: {e}")
        return False, f"Shell read error: {e}", None

    if new_cwd:
        log_debug(f"CWD updated to: {new_cwd}")

    truncated = truncate_output(output_lines)
    return True, '\n'.join(truncated), new_cwd


def parse_and_validate_commands(content: str) -> List[Tuple[str, bool, str]]:
    """Parseia comandos de uma string e valida segurança de cada um.

    Tenta parsear como JSON, depois como Python literal (ast.literal_eval),
    e como fallback faz parse linha a linha. Cada comando encontrado é
    validado com ``validate_command_safety``.

    Args:
        content: String contendo comandos (JSON, Python literal, ou texto plano).

    Returns:
        Lista de tuplas ``(command, is_safe, reason)``.
    """
    results: List[Tuple[str, bool, str]] = []

    try:
        # Tenta JSON primeiro (formato preferido da LLM)
        try:
            commands = json.loads(content)
        except json.JSONDecodeError:
            # Fallback para Python literal (listas formatadas sem JSON válido)
            commands = ast.literal_eval(content)

        if not isinstance(commands, list):
            commands = [str(commands)]

        for cmd in commands:
            if isinstance(cmd, str):
                is_safe, reason = validate_command_safety(cmd)
                results.append((cmd, is_safe, reason))

    except Exception as e:
        log_debug(f"Error parsing commands: {str(e)}", content)
        # Último recurso: parse linha a linha
        for line in content.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                is_safe, reason = validate_command_safety(line)
                results.append((line, is_safe, reason))

    return results


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: kollzshd_commands.py <user_query>", file=sys.stderr)
        sys.exit(1)

    user_query = sys.argv[1]
    print(f"is_readonly test: {is_readonly(user_query)}")

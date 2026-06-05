"""Configuracao centralizada de logging para o daemon kollzsh."""

import logging

LOG_FILE: str = "/tmp/kollzsh_debug.log"

_configured: bool = False


def setup_logging(log_file: str = LOG_FILE) -> None:
    """Inicializa o logging uma unica vez.

    Args:
        log_file: Caminho para o arquivo de log.
    """
    global _configured
    if _configured:
        return
    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _configured = True


def log_debug(message: str, data: str | None = None) -> None:
    """Registra mensagem de debug no log do daemon.

    Args:
        message: Mensagem principal.
        data: Dados adicionais opcionais (payload, output, etc).
    """
    if data is not None:
        logging.debug(
            f"{message}\nData: {data}\n"
            "----------------------------------------",
        )
    else:
        logging.debug(message)

import os
from typing import Callable, List, Optional

from kollzshd_commands import truncate_output
from kollzshd_llm import build_navigation_prompt, extract_commands, call_llm
from pi_client import run_pi_query
from shell_manager import ShellManager

EventSender = Callable[..., None]


class AgentRouter:
    def __init__(self, shell: ShellManager) -> None:
        self.shell = shell

    def run_navigation(
        self, query: str, event_sender: Optional[EventSender] = None
    ) -> List[str]:
        try:
            payload = build_navigation_prompt(self.shell.cwd, query)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"Prompt building failed: {exc}")
            return [f"Error building prompt: {exc}"]

        try:
            response_data = call_llm(payload)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"LLM call failed: {exc}")
            return [f"LLM call error: {exc}"]

        if not response_data:
            if event_sender:
                event_sender("error", msg="LLM returned empty response")
            return ["Error: LLM returned no response"]

        try:
            commands = extract_commands(response_data)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"Failed to parse LLM response: {exc}")
            return [f"Parse error: {exc}"]

        if not commands:
            return ["No relevant commands found"]

        output: list[str] = []
        for cmd in commands:
            if event_sender:
                event_sender("cmd", cmd=cmd)
            try:
                success, cmd_output, new_cwd = self.shell.execute_command(cmd)
            except Exception as exc:
                if event_sender:
                    event_sender("error", msg=f"Command failed: {cmd} - {exc}")
                continue
            if not success and not self.shell.is_alive:
                self.shell.start_shell()
            if new_cwd:
                self.shell.update_cwd(new_cwd)
            if cmd_output:
                output.extend(cmd_output.strip().split("\n"))

        return truncate_output(output)

    def run_deep_pi(
        self, query: str, event_sender: Optional[EventSender] = None
    ) -> List[str]:
        plugin_dir = os.environ.get(
            "KOLLZSH_PLUGIN_DIR",
            os.path.dirname(os.path.abspath(__file__)),
        )
        agent_dir = os.environ.get(
            "KOLLZSH_PI_AGENT_DIR",
            os.path.expanduser("~/.pi/agent"),
        )
        url = os.environ.get("KOLLZSH_URL", "http://localhost:8080")
        model = os.environ.get(
            "KOLLZSH_MODEL",
            "unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL",
        )
        max_turns = int(os.environ.get("KOLLZSH_PI_MAX_TURNS", "6"))
        context_level = os.environ.get("KOLLZSH_PI_CONTEXT_LEVEL", "level3")

        try:
            lines = run_pi_query(
                self.shell.cwd, query, plugin_dir, agent_dir,
                url, model, max_turns, context_level,
                event_callback=event_sender,
            )
            return truncate_output(lines)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"Pi query failed: {exc}")
            return [f"Deep search error: {exc}"]

    def run_agent_loop(
        self, query: str, mode: str = "navigation",
        event_sender: Optional[EventSender] = None,
    ) -> List[str]:
        if mode == "deep":
            return self.run_deep_pi(query, event_sender)
        return self.run_navigation(query, event_sender)

"""Terminal client `samambaia` (and vowel alias `aaaia`) with multi-turn chat, warning prompt, and deferred queue."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import readline  # noqa: F401
import sys
from typing import Any

import httpx

from samambaia.config import load_config
from samambaia.coordinator import BrowserMcpType, execute_coordinated_turn, plan_turn
from samambaia.deferred import (
    add_deferred_task,
    complete_deferred_task,
    drop_deferred_task,
    get_deferred_task,
    list_deferred_tasks,
)
from samambaia.sessions import (
    append_turn,
    create_session,
    format_history_context,
    get_or_create_last_session,
    load_turns,
)

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"


def _invoked_prog() -> str:
    base = os.path.basename(sys.argv[0] or "samambaia")
    return base if base in ("samambaia", "aaaia") else "samambaia"


def _render_event(
    ev: dict[str, Any],
    *,
    quiet_meta: bool = False,
    captured_tokens: list[str] | None = None,
) -> int:
    kind = ev.get("event")
    if kind == "route" and not quiet_meta:
        print(
            f"{CYAN}[{ev.get('task_type')} -> {ev.get('model')}]{RESET} "
            f"{DIM}({ev.get('reason')}){RESET}",
            file=sys.stderr,
        )
    elif kind == "tool" and not quiet_meta:
        args_preview = json.dumps(ev.get("args", {}), ensure_ascii=False)
        if len(args_preview) > 100:
            args_preview = args_preview[:97] + "..."
        print(
            f"{YELLOW}  ⚙ {ev.get('name')}{RESET}{DIM} {args_preview}{RESET}",
            file=sys.stderr,
        )
    elif kind == "token":
        text = ev.get("text", "")
        if captured_tokens is not None:
            captured_tokens.append(text)
        sys.stdout.write(text)
        sys.stdout.flush()
    elif kind == "error":
        print(f"\n{RED}[Erro] {ev.get('message')}{RESET}", file=sys.stderr)
        return 1
    return 0


def _prompt_heavy_task_choice(plan: dict[str, Any]) -> str:
    if not sys.stderr.isatty():
        return "continue"

    close_stream = False
    if sys.stdin.isatty():
        tty_in = sys.stdin
    else:
        try:
            tty_in = open("/dev/tty", "r", encoding="utf-8")
            close_stream = True
        except OSError:
            return "continue"

    reasons_str = "; ".join(plan.get("warning_reasons") or [])
    print(
        f"\n{YELLOW}{BOLD}⚠ Aviso: Tarefa longa ou troca para modelo pesado detectada!{RESET}\n"
        f"  • {CYAN}Perfil / Modelo:{RESET} {plan.get('task_type')} -> {BOLD}{plan.get('model')}{RESET}\n"
        f"  • {CYAN}Motivo:{RESET}          {plan.get('reason')}\n"
        f"  • {CYAN}Alerta:{RESET}          {reasons_str}\n\n"
        f"  {GREEN}[C]{RESET} Continuar agora {DIM}(Enter){RESET}\n"
        f"  {YELLOW}[D]{RESET} Deixar para depois {DIM}(salvar na fila pendente){RESET}\n"
        f"  {RED}[X]{RESET} Cancelar",
        file=sys.stderr,
    )

    try:
        while True:
            sys.stderr.write(f"{BOLD}Escolha [C/d/x]: {RESET}")
            sys.stderr.flush()
            raw = tty_in.readline()
            if raw == "":
                print(file=sys.stderr)
                return "cancel"
            ans = raw.strip().lower()
            if ans in ("", "c", "continuar", "s", "sim", "y", "yes", "1"):
                print(file=sys.stderr)
                return "continue"
            if ans in ("d", "depois", "l", "later", "2"):
                print(file=sys.stderr)
                return "defer"
            if ans in ("x", "n", "nao", "não", "cancel", "cancelar", "q", "3"):
                print(file=sys.stderr)
                return "cancel"
            print(
                f"{DIM}Digite 'c' para continuar, 'd' para deixar para depois ou 'x' para cancelar.{RESET}",
                file=sys.stderr,
            )
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return "cancel"
    finally:
        if close_stream:
            tty_in.close()


def _print_status(daemon_url: str, prog: str) -> int:
    pending = list_deferred_tasks()
    try:
        resp = httpx.get(f"{daemon_url}/status", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        print(
            f"{YELLOW}Daemon HTTP offline em {daemon_url}{RESET} "
            f"(o comando `{prog}` ainda funciona em modo local direto, ou suba o serviço systemd)."
        )
        return 0

    print(f"{GREEN}● Daemon:{RESET}  {data.get('daemon')} ({daemon_url})")
    print(f"{GREEN}● Web UI:{RESET}  {data.get('web_ui')} (Open WebUI)")
    print(f"{GREEN}● Ollama:{RESET}  {'online' if data.get('ollama_online') else 'OFFLINE'} ({data.get('ollama_host')})")
    if data.get("installed_models"):
        print(f"\n{CYAN}Modelos instalados no Ollama:{RESET}")
        for m in data["installed_models"]:
            print(f"  - {m}")
    if data.get("active_tiers"):
        print(f"\n{CYAN}Roteamento ativo por perfil:{RESET}")
        for tier, model in data["active_tiers"].items():
            print(f"  - {tier:<10} -> {model}")
    if data.get("loaded_models"):
        print(f"\n{CYAN}Modelos atualmente carregados em RAM/VRAM:{RESET}")
        for m in data["loaded_models"]:
            total_gb = m.get("size", 0) / (1024**3)
            vram_gb = m.get("size_vram", 0) / (1024**3)
            print(f"  - {m['name']} (Total: {total_gb:.1f} GiB | VRAM GPU: {vram_gb:.1f} GiB)")
    print(f"\n{CYAN}Fila 'Deixar para depois':{RESET} {len(pending)} pendente(s) (use `{prog} --later`)")
    return 0


def _print_deferred_list(prog: str) -> int:
    items = list_deferred_tasks(include_done=True)
    pending = [i for i in items if i.get("status") == "pending"]
    done = [i for i in items if i.get("status") == "done"][-5:]

    if not pending and not done:
        print("Nenhuma tarefa salva na fila 'Deixar para depois'.")
        return 0

    if pending:
        print(f"{YELLOW}{BOLD}Tarefas pendentes ({len(pending)}):{RESET}")
        for item in pending:
            preview = item["prompt"].replace("\n", " ")
            if len(preview) > 75:
                preview = preview[:72] + "..."
            print(
                f"  {BOLD}#{item['id']}{RESET} [{item['task_type']} -> {item['model']}] "
                f"{DIM}({item['created_at']} | {item['cwd']}){RESET}\n"
                f"     \"{preview}\""
            )
        print(
            f"\n{DIM}Executar todas: `{prog} --run-later` | Específica: `{prog} --run-later <ID>` | Remover: `{prog} --drop-later <ID>`{RESET}"
        )

    if done:
        print(f"\n{GREEN}Últimas concluídas:{RESET}")
        for item in done:
            print(
                f"  #{item['id']} ✔ concluída em {item.get('completed_at', '?')} -> {item.get('result_path')}"
            )
    return 0


def _stream_execution(
    daemon_url: str,
    *,
    prompt: str,
    cwd: str,
    task_type: str | None,
    model: str | None,
    reason: str | None = None,
    history_context: str | None = None,
    browser_mcp: BrowserMcpType | None = None,
    quiet_meta: bool = False,
    capture_output: bool = False,
) -> tuple[int, str]:
    payload = {
        "prompt": prompt,
        "cwd": cwd,
        "task_type": task_type,
        "model": model,
        "reason": reason,
        "history_context": history_context,
        "browser_mcp": browser_mcp,
    }
    captured: list[str] | None = [] if capture_output else None

    try:
        with httpx.Client(timeout=None) as client:
            with client.stream("POST", f"{daemon_url}/ask", json=payload) as resp:
                resp.raise_for_status()
                exit_code = 0
                for line in resp.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    rc = _render_event(
                        ev, quiet_meta=quiet_meta, captured_tokens=captured
                    )
                    if rc != 0:
                        exit_code = rc
                print()
                return exit_code, "".join(captured or [])
    except httpx.ConnectError:
        if not quiet_meta:
            print(
                f"{DIM}[daemon offline -> executando diretamente]{RESET}",
                file=sys.stderr,
            )

        async def _inproc() -> int:
            code = 0
            async for ev in execute_coordinated_turn(
                prompt=prompt,
                cwd=cwd,
                force_task_type=task_type,  # type: ignore[arg-type]
                force_model=model,
                precomputed_reason=reason,
                history_context=history_context,
                browser_mcp=browser_mcp,
            ):
                rc = _render_event(
                    ev, quiet_meta=quiet_meta, captured_tokens=captured
                )
                if rc != 0:
                    code = rc
            print()
            return code

        rc = asyncio.run(_inproc())
        return rc, "".join(captured or [])


def _compute_plan(
    daemon_url: str,
    *,
    prompt: str,
    cwd: str,
    task_type: str | None,
    model: str | None,
    history_context: str | None = None,
    browser_mcp: BrowserMcpType | None = None,
) -> dict[str, Any]:
    payload = {
        "prompt": prompt,
        "cwd": cwd,
        "task_type": task_type,
        "model": model,
        "history_context": history_context,
        "browser_mcp": browser_mcp,
    }
    try:
        resp = httpx.post(f"{daemon_url}/plan", json=payload, timeout=300.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        return asyncio.run(
            plan_turn(
                prompt=prompt,
                cwd=cwd,
                force_task_type=task_type,  # type: ignore[arg-type]
                force_model=model,
                history_context=history_context,
                browser_mcp=browser_mcp,
            )
        )


def _execute_interactive_turn(
    daemon_url: str,
    *,
    prog: str,
    prompt: str,
    cwd: str,
    tier: str | None,
    model: str | None,
    auto_yes: bool,
    quiet: bool,
    session_id: str | None,
    browser_mcp: BrowserMcpType | None,
) -> int:
    history_ctx = ""
    if session_id:
        history_ctx = format_history_context(load_turns(session_id))

    plan = _compute_plan(
        daemon_url,
        prompt=prompt,
        cwd=cwd,
        task_type=tier,
        model=model,
        history_context=history_ctx or None,
        browser_mcp=browser_mcp,
    )

    if plan.get("needs_confirmation") and not auto_yes:
        choice = _prompt_heavy_task_choice(plan)
        if choice == "cancel":
            print(f"{DIM}Operação cancelada.{RESET}", file=sys.stderr)
            return 130
        if choice == "defer":
            saved = add_deferred_task(
                prompt=prompt,
                cwd=plan["cwd"],
                task_type=plan["task_type"],
                model=plan["model"],
                reason=plan.get("reason", ""),
            )
            print(
                f"{GREEN}✔ Tarefa #{saved['id']} salva para depois!{RESET}\n"
                f"  • Ver fila:  {CYAN}{prog} --later{RESET}\n"
                f"  • Executar:  {CYAN}{prog} --run-later {saved['id']}{RESET} {DIM}(ou `{prog} --run-later` para rodar todas){RESET}",
                file=sys.stderr,
            )
            return 0

    rc, answer = _stream_execution(
        daemon_url,
        prompt=prompt,
        cwd=plan["cwd"],
        task_type=plan["task_type"],
        model=plan["model"],
        reason=plan.get("reason"),
        history_context=history_ctx or None,
        browser_mcp=browser_mcp,
        quiet_meta=quiet,
        capture_output=session_id is not None,
    )
    if rc == 0 and session_id and answer.strip():
        append_turn(
            session_id,
            plan["cwd"],
            prompt,
            answer,
            task_type=plan["task_type"],
            model=plan["model"],
        )
    return rc


def _run_chat_repl(
    daemon_url: str,
    *,
    prog: str,
    cwd: str,
    resume_existing: bool,
    tier: str | None,
    model: str | None,
    auto_yes: bool,
    browser_mcp: BrowserMcpType | None,
) -> int:
    session_id = (
        get_or_create_last_session(cwd) if resume_existing else create_session(cwd)
    )
    existing_turns = len(load_turns(session_id))
    print(
        f"{GREEN}{BOLD}🌿 Samambaia — Sessão de conversa ativa{RESET} {DIM}(id={session_id[:8]} | turnos anteriores={existing_turns} | pasta={cwd}){RESET}\n"
        f"{DIM}Comandos: /new (nova conversa) | /later (ver fila) | /exit (sair){RESET}"
    )

    while True:
        try:
            user_input = input(f"\n{CYAN}{BOLD}você>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(
                f"\n{DIM}Até mais! (Use `{prog} -c \"...\"` ou `{prog} --chat -c` para retomar){RESET}"
            )
            return 0

        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit", "sair"):
            print(
                f"{DIM}Sessão salva. Use `{prog} -c \"...\"` ou `{prog} --chat -c` para retomar.{RESET}"
            )
            return 0
        if user_input.lower() == "/new":
            session_id = create_session(cwd)
            print(f"{GREEN}✔ Nova sessão iniciada ({session_id[:8]}).{RESET}")
            continue
        if user_input.lower() == "/later":
            _print_deferred_list(prog)
            continue

        _execute_interactive_turn(
            daemon_url,
            prog=prog,
            prompt=user_input,
            cwd=cwd,
            tier=tier,
            model=model,
            auto_yes=auto_yes,
            quiet=False,
            session_id=session_id,
            browser_mcp=browser_mcp,
        )


def _run_deferred(daemon_url: str, target: str) -> int:
    if target == "all":
        tasks = list_deferred_tasks()
        if not tasks:
            print("Nenhuma tarefa pendente na fila.")
            return 0
    else:
        try:
            tid = int(target.lstrip("#"))
        except ValueError:
            print(f"{RED}ID inválido: {target}. Use um número ou 'all'.{RESET}", file=sys.stderr)
            return 1
        item = get_deferred_task(tid)
        if not item:
            print(f"{RED}Tarefa #{tid} não encontrada.{RESET}", file=sys.stderr)
            return 1
        tasks = [item]

    overall_rc = 0
    for item in tasks:
        tid = int(item["id"])
        print(
            f"\n{CYAN}{BOLD}▶ Executando tarefa pendente #{tid}{RESET} "
            f"{DIM}({item['cwd']}){RESET}",
            file=sys.stderr,
        )
        rc, text = _stream_execution(
            daemon_url,
            prompt=item["prompt"],
            cwd=item["cwd"],
            task_type=item["task_type"],
            model=item["model"],
            reason=f"fila pendente #{tid}: {item.get('reason', '')}",
            capture_output=True,
        )
        if rc == 0:
            saved_path = complete_deferred_task(tid, text)
            print(
                f"{GREEN}✔ Tarefa #{tid} concluída! Resposta salva em: {saved_path}{RESET}",
                file=sys.stderr,
            )
        else:
            overall_rc = rc
    return overall_rc


def main() -> None:
    prog = _invoked_prog()
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Samambaia (samambAIA / aaaia) — Cooperative Local AI Agent Coordinator",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt for the agent (also accepts piped stdin).",
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Continues the last conversation started in this directory.",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Opens an interactive multi-turn chat REPL in the terminal.",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="Starts a fresh conversation session in this directory.",
    )
    parser.add_argument(
        "--browser-mcp",
        choices=["chrome", "firefox"],
        default=None,
        help="Attaches browser MCP server on demand (chrome = chrome-devtools-mcp | firefox = @playwright/mcp).",
    )
    parser.add_argument(
        "-t",
        "--tier",
        choices=["direct", "terminal", "pr_review", "coding", "deep"],
        default=None,
        help="Forces a specific specialist profile bypassing the router.",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="Forces a specific Ollama model tag (e.g. qwen3.5:35b-a3b).",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skips confirmation prompt on long/deep tasks.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppresses routing header and tool call logs on stderr.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Shows daemon status, Web UI URL, installed models, VRAM, and deferred queue.",
    )
    parser.add_argument(
        "--later",
        action="store_true",
        help="Lists tasks saved in the 'save for later' queue.",
    )
    parser.add_argument(
        "--run-later",
        nargs="?",
        const="all",
        metavar="ID",
        help="Executes all deferred tasks (or a specific task ID).",
    )
    parser.add_argument(
        "--drop-later",
        type=int,
        metavar="ID",
        help="Removes a deferred task by ID.",
    )
    parser.add_argument(
        "--sync-zk",
        action="store_true",
        help="Incrementally syncs ~/zk notes into Open WebUI's '#zk' Knowledge collection.",
    )
    args = parser.parse_args()

    cfg = load_config()
    daemon_url = f"http://{cfg.daemon_host}:{cfg.daemon_port}"
    cwd = os.getcwd()

    if args.status:
        sys.exit(_print_status(daemon_url, prog))
    if args.sync_zk:
        from samambaia.zk_sync import sync_zk_to_openwebui

        sys.exit(sync_zk_to_openwebui())
    if args.later:
        sys.exit(_print_deferred_list(prog))
    if args.drop_later is not None:
        if drop_deferred_task(args.drop_later):
            print(f"{GREEN}✔ Tarefa #{args.drop_later} removida da fila.{RESET}")
            sys.exit(0)
        print(f"{RED}Tarefa #{args.drop_later} não encontrada.{RESET}", file=sys.stderr)
        sys.exit(1)
    if args.run_later is not None:
        sys.exit(_run_deferred(daemon_url, args.run_later))

    if args.chat:
        sys.exit(
            _run_chat_repl(
                daemon_url,
                prog=prog,
                cwd=cwd,
                resume_existing=args.continue_session and not args.new,
                tier=args.tier,
                model=args.model,
                auto_yes=args.yes,
                browser_mcp=args.browser_mcp,
            )
        )

    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()

    user_prompt = " ".join(args.prompt).strip()
    if stdin_text and user_prompt:
        full_prompt = f"{user_prompt}\n\n---\n{stdin_text}"
    elif stdin_text:
        full_prompt = stdin_text
    else:
        full_prompt = user_prompt

    if not full_prompt:
        parser.print_help()
        sys.exit(1)

    if args.new or not args.continue_session:
        session_id = create_session(cwd)
    else:
        session_id = get_or_create_last_session(cwd)

    rc = _execute_interactive_turn(
        daemon_url,
        prog=prog,
        prompt=full_prompt,
        cwd=cwd,
        tier=args.tier,
        model=args.model,
        auto_yes=args.yes,
        quiet=args.quiet,
        session_id=session_id,
        browser_mcp=args.browser_mcp,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()

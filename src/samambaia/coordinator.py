"""Two-stage coordinator: Fast JSON-schema Router (Ollama) + Specialist Agents (Antigravity SDK)."""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
from typing import Any, AsyncIterator, Callable, Literal

import httpx
import pydantic
from google.antigravity import (
    Agent,
    AgentBehavior,
    BuiltinTools,
    CapabilitiesConfig,
    CompactionConfig,
    LocalOpenAIAgentConfig,
    types,
)
from google.antigravity.hooks import policy

from samambaia.config import (
    APP_DATA_DIR,
    SAVE_DIR,
    AssistantConfig,
    fetch_loaded_models,
    load_config,
    resolve_all_tiers,
    unload_model,
)
from samambaia.github_tools import make_github_pr_tools
from samambaia.integrations.zk import make_zk_tools
from samambaia.openai_bridge import temporary_bridge_server
from samambaia.web_tools import make_web_tools

TaskType = Literal["direct", "terminal", "pr_review", "coding", "deep"]
BrowserMcpType = Literal["chrome", "firefox"]


class RouteDecision(pydantic.BaseModel):
    task_type: TaskType
    reason: str
    estimated_long: bool = False


def _make_dangerous_command_predicate(
    patterns: list[str],
) -> Callable[[dict[str, Any]], bool]:
    tuple_pats = tuple(patterns)

    def _is_dangerous(args: dict[str, Any]) -> bool:
        cmd = str(
            args.get("CommandLine")
            or args.get("command")
            or args.get("cmd")
            or ""
        ).strip()
        return any(pat in cmd for pat in tuple_pats)

    return _is_dangerous


def _find_npx_binary() -> str:
    found = shutil.which("npx")
    if found:
        return found
    home = pathlib.Path.home()
    for pattern in (
        ".config/nvm/versions/node/*/bin/npx",
        ".nvm/versions/node/*/bin/npx",
        ".local/share/mise/shims/npx",
    ):
        matches = sorted(home.glob(pattern))
        if matches:
            return str(matches[-1])
    return "npx"


def make_browser_mcp_servers(
    browser_mcp: BrowserMcpType | None,
    cfg: AssistantConfig,
) -> list[types.McpServerConfig]:
    if not browser_mcp:
        return []
    npx_bin = _find_npx_binary()
    npx_dir = os.path.dirname(npx_bin)
    mcp_env = dict(os.environ)
    if npx_dir:
        mcp_env["PATH"] = f"{npx_dir}:{mcp_env.get('PATH', '')}"

    if browser_mcp == "chrome":
        return [
            types.McpStdioServer(
                name="chrome_devtools",
                command=npx_bin,
                args=list(cfg.features.chrome_mcp_args),
                env=mcp_env,
            )
        ]
    if browser_mcp == "firefox":
        return [
            types.McpStdioServer(
                name="playwright_firefox",
                command=npx_bin,
                args=list(cfg.features.firefox_mcp_args),
                env=mcp_env,
            )
        ]
    return []


def make_readonly_git_tools(
    cwd: str, max_diff_chars: int = 24000
) -> list[Callable[..., Any]]:
    def git_status() -> str:
        """Returns the current Git branch and modified files in the repository."""
        res = subprocess.run(
            ["git", "-C", cwd, "status", "--short", "--branch"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return res.stdout.strip() or res.stderr.strip() or "Working tree clean."

    def git_diff(target: str = "HEAD") -> str:
        """Returns the git diff of the current directory against a target ref (e.g. 'HEAD', 'main', 'HEAD~1').

        Args:
            target: Git reference to compare against (default: 'HEAD').
        """
        res = subprocess.run(
            ["git", "-C", cwd, "diff", target],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        out = res.stdout or res.stderr or "(no differences against target)"
        if len(out) > max_diff_chars:
            return (
                out[:max_diff_chars]
                + f"\n\n[... diff truncated at {max_diff_chars} chars; use view_file to inspect specific files ...]"
            )
        return out

    def git_log(limit: int = 5) -> str:
        """Lists the most recent commits in the Git repository.

        Args:
            limit: Maximum number of commits to display (1 to 20).
        """
        safe_limit = max(1, min(int(limit), 20))
        res = subprocess.run(
            ["git", "-C", cwd, "log", f"-n{safe_limit}", "--oneline", "--decorate"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return res.stdout.strip() or res.stderr.strip()

    return [git_status, git_diff, git_log]


def _compose_with_history(prompt: str, history_context: str | None) -> str:
    if not history_context or not history_context.strip():
        return prompt
    return (
        "Contexto resumido da conversa anterior com o usuário:\n"
        f"{history_context.strip()}\n\n"
        "===\n"
        f"Pedido atual do usuário:\n{prompt}"
    )


async def classify_prompt(
    prompt: str,
    router_model: str,
    cfg: AssistantConfig,
    history_context: str | None = None,
) -> RouteDecision:
    zk_hint = (
        "pesquisar/ler/anotar no Zettelkasten pessoal (~/zk, notas, TODO), "
        if cfg.zettelkasten.enabled
        else ""
    )
    zk_direct_exclusion = ", notas zk" if cfg.zettelkasten.enabled else ""

    system_msg = (
        "Você é um roteador de tarefas rápido. Classifique o pedido ATUAL do usuário em EXATAMENTE uma categoria:\n"
        f"- 'direct': traduções, explicações conceituais, reescrita/resumo de texto já presente na conversa, perguntas que NÃO dependem de ler arquivos locais{zk_direct_exclusion}, buscar na internet nem rodar comandos.\n"
        f"- 'terminal': {zk_hint}pesquisar na internet/web, ler páginas/URLs (http/https), rodar comandos no terminal/shell, checar processos, memória, disco, containers podman/docker, rede ou executar git/cli.\n"
        "- 'pr_review': revisar Pull Request (local ou remoto pelo número ex: PR #42), code review, analisar git diff/commit ou auditar mudanças recentes de código.\n"
        "- 'coding': criar novos arquivos de código, editar arquivos existentes, implementar funções ou escrever testes unitários.\n"
        "- 'deep': análise arquitetural extensa, auditoria profunda de múltiplos módulos ou investigação complexa sem pressa.\n\n"
        "Defina 'estimated_long' como true SE a tarefa for 'deep', envolver auditar/refatorar múltiplos arquivos do repositório inteiro "
        "ou exigir vários minutos; caso contrário defina false. Escreva 'reason' no mesmo idioma do usuário."
    )
    user_payload = prompt
    if history_context:
        tail = history_context[-1500:]
        user_payload = f"[Contexto recente]\n{tail}\n\n[Pedido atual]\n{prompt}"

    async with httpx.AsyncClient(
        timeout=cfg.inference.ollama_timeout_seconds
    ) as client:
        resp = await client.post(
            f"{cfg.ollama_host.rstrip('/')}/api/chat",
            json={
                "model": router_model,
                "stream": False,
                "think": False,
                "keep_alive": cfg.inference.router_keep_alive,
                "format": RouteDecision.model_json_schema(),
                "options": {
                    "temperature": 0.0,
                    "num_ctx": cfg.inference.router_num_ctx,
                },
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_payload},
                ],
            },
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return RouteDecision.model_validate_json(content)


async def stream_direct_ollama(
    prompt: str,
    model_name: str,
    cfg: AssistantConfig,
    history_context: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    system_msg = (
        "Você é um assistente técnico conciso, direto e preciso. "
        "Para traduções, entregue a tradução natural preservando termos técnicos quando apropriado. "
        f"Responda no idioma solicitado pelo usuário (padrão: {cfg.default_language})."
    )
    full_user = _compose_with_history(prompt, history_context)
    async with httpx.AsyncClient(
        timeout=cfg.inference.ollama_timeout_seconds
    ) as client:
        async with client.stream(
            "POST",
            f"{cfg.ollama_host.rstrip('/')}/api/chat",
            json={
                "model": model_name,
                "stream": True,
                "think": False,
                "keep_alive": cfg.inference.router_keep_alive,
                "options": {
                    "temperature": cfg.inference.direct_temperature,
                    "num_ctx": cfg.inference.direct_num_ctx,
                },
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": full_user},
                ],
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield {"event": "token", "text": token}


def build_specialist_config(
    task_type: TaskType,
    model_name: str,
    cwd: str,
    cfg: AssistantConfig,
    base_url: str,
    browser_mcp: BrowserMcpType | None = None,
) -> LocalOpenAIAgentConfig:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

    web_tools = make_web_tools(cfg) if cfg.features.enable_web_tools else []
    zk_tools = make_zk_tools(cfg) if cfg.zettelkasten.enabled else []
    gh_pr_tools = (
        make_github_pr_tools(cwd, cfg)
        if cfg.features.enable_github_pr_tools
        else []
    )
    git_tools = make_readonly_git_tools(cwd, cfg.limits.max_diff_chars)
    mcp_servers = make_browser_mcp_servers(browser_mcp, cfg)

    zk_instruction = (
        "Para notas pessoais ou TODO do Zettelkasten, use search_zk, read_zk_note e append_zk_todo. "
        if cfg.zettelkasten.enabled
        else ""
    )

    if task_type == "pr_review":
        enabled_tools = [
            BuiltinTools.VIEW_FILE,
            BuiltinTools.LIST_DIR,
            BuiltinTools.SEARCH_DIR,
            BuiltinTools.FIND_FILE,
        ]
        custom_tools = [*git_tools, *gh_pr_tools, *web_tools]
        task_policies = [policy.allow_all()]
        instructions = (
            f"Você é um Staff Engineer fazendo Code Review no repositório '{cwd}'. "
            "Para PRs remotos do GitHub (ex: 'PR #128'), use gh_pr_view e gh_pr_diff. "
            "Para mudanças locais, use git_status, git_log e git_diff, além de view_file / search_directory "
            "para verificar chamadores e tipagem. Aponte apenas problemas reais (CRÍTICO, AVISO ou SUGESTÃO)."
        )
    elif task_type == "terminal":
        enabled_tools = [
            BuiltinTools.RUN_COMMAND,
            BuiltinTools.VIEW_FILE,
            BuiltinTools.LIST_DIR,
            BuiltinTools.FIND_FILE,
            BuiltinTools.SEARCH_DIR,
        ]
        custom_tools = [*web_tools, *zk_tools]
        task_policies = [
            policy.allow_all(),
            policy.deny(
                "run_command",
                when=_make_dangerous_command_predicate(
                    cfg.guard.dangerous_command_patterns
                ),
                name="block_destructive_commands",
            ),
        ]
        instructions = (
            f"Você é um especialista Linux operando em '{cwd}'. "
            f"{zk_instruction}"
            "Para internet, use web_search e read_webpage. Para o sistema local, use run_command "
            "(nota: ao consultar containers sem TTY via podman/docker exec, não passe '-it')."
        )
    elif task_type == "coding":
        enabled_tools = [
            BuiltinTools.VIEW_FILE,
            BuiltinTools.CREATE_FILE,
            BuiltinTools.EDIT_FILE,
            BuiltinTools.LIST_DIR,
            BuiltinTools.SEARCH_DIR,
            BuiltinTools.FIND_FILE,
        ]
        custom_tools = [*web_tools, *zk_tools]
        task_policies = [policy.confirm_run_command()]
        instructions = (
            f"Você é um engenheiro de software sênior trabalhando em '{cwd}'. "
            "Leia o código existente antes de editar/criar arquivos e use web_search / read_webpage se precisar consultar documentações recentes."
        )
    else:  # deep
        enabled_tools = [
            BuiltinTools.VIEW_FILE,
            BuiltinTools.LIST_DIR,
            BuiltinTools.SEARCH_DIR,
            BuiltinTools.FIND_FILE,
        ]
        custom_tools = [*git_tools, *gh_pr_tools, *web_tools, *zk_tools]
        task_policies = [policy.allow_all()]
        instructions = (
            f"Você é um arquiteto de software realizando uma análise técnica profunda em '{cwd}'. "
            "Explore a estrutura de diretórios, arquivos relevantes e documentações antes de sintetizar sua análise."
        )

    return LocalOpenAIAgentConfig(
        model=model_name,
        base_url=base_url,
        workspaces=[cwd],
        save_dir=str(SAVE_DIR),
        app_data_dir=str(APP_DATA_DIR),
        capabilities=CapabilitiesConfig(
            agent_behavior=AgentBehavior.MINIMAL,
            enable_subagents=False,
            enabled_tools=enabled_tools,
        ),
        compaction_config=CompactionConfig(
            token_threshold=cfg.inference.compaction_tokens
        ),
        tools=custom_tools,
        mcp_servers=mcp_servers,
        policies=task_policies,
        system_instructions=instructions,
    )


async def plan_turn(
    prompt: str,
    cwd: str | None = None,
    force_task_type: TaskType | None = None,
    force_model: str | None = None,
    history_context: str | None = None,
    browser_mcp: BrowserMcpType | None = None,
) -> dict[str, Any]:
    cfg = load_config()
    resolved_cwd = str(os.path.abspath(cwd or os.getcwd()))
    if not os.path.isdir(resolved_cwd):
        resolved_cwd = os.path.expanduser("~")

    _, tier_models = await resolve_all_tiers(cfg)
    router_model = tier_models["router"]

    if browser_mcp and force_task_type is None:
        task_type: TaskType = "terminal"
        reason = f"inspeção de navegador via MCP ({browser_mcp})"
        estimated_long = False
    elif force_task_type is not None:
        task_type = force_task_type
        reason = "perfil selecionado manualmente"
        estimated_long = task_type in cfg.guard.always_confirm_tiers
    else:
        decision = await classify_prompt(
            prompt, router_model, cfg, history_context=history_context
        )
        task_type = decision.task_type
        reason = decision.reason
        estimated_long = decision.estimated_long

    target_model = force_model or tier_models[task_type]

    loaded = await fetch_loaded_models(cfg.ollama_host)
    loaded_names = {m.get("name") for m in loaded if m.get("name")}
    model_already_loaded = target_model in loaded_names
    is_heavy_model = any(
        tag in target_model for tag in cfg.guard.heavy_model_patterns
    )

    warning_reasons: list[str] = []
    if task_type in cfg.guard.always_confirm_tiers:
        warning_reasons.append(f"modo '{task_type}' (análise profunda)")
    if is_heavy_model and not model_already_loaded:
        warning_reasons.append(
            f"troca para modelo grande '{target_model}' (carregamento a frio na RAM/VRAM)"
        )
    if estimated_long and task_type not in cfg.guard.always_confirm_tiers:
        warning_reasons.append("tarefa estimada como longa / múltiplos passos")

    return {
        "task_type": task_type,
        "model": target_model,
        "router_model": router_model,
        "reason": reason,
        "estimated_long": estimated_long,
        "model_already_loaded": model_already_loaded,
        "needs_confirmation": len(warning_reasons) > 0,
        "warning_reasons": warning_reasons,
        "cwd": resolved_cwd,
    }


async def execute_coordinated_turn(
    prompt: str,
    cwd: str | None = None,
    force_task_type: TaskType | None = None,
    force_model: str | None = None,
    bridge_base_url: str | None = None,
    precomputed_reason: str | None = None,
    history_context: str | None = None,
    browser_mcp: BrowserMcpType | None = None,
) -> AsyncIterator[dict[str, Any]]:
    cfg = load_config()
    resolved_cwd = str(os.path.abspath(cwd or os.getcwd()))
    if not os.path.isdir(resolved_cwd):
        resolved_cwd = os.path.expanduser("~")

    _, tier_models = await resolve_all_tiers(cfg)
    router_model = tier_models["router"]

    if browser_mcp and force_task_type is None:
        task_type: TaskType = "terminal"
        reason = precomputed_reason or f"inspeção de navegador via MCP ({browser_mcp})"
    elif force_task_type is not None:
        task_type = force_task_type
        reason = precomputed_reason or "perfil selecionado manualmente"
    else:
        decision = await classify_prompt(
            prompt, router_model, cfg, history_context=history_context
        )
        task_type = decision.task_type
        reason = decision.reason

    target_model = force_model or tier_models[task_type]

    yield {
        "event": "route",
        "task_type": task_type,
        "model": target_model,
        "reason": reason,
        "cwd": resolved_cwd,
    }

    if task_type == "direct":
        async for ev in stream_direct_ollama(
            prompt, target_model, cfg, history_context=history_context
        ):
            yield ev
        yield {"event": "done"}
        return

    if cfg.inference.unload_other_models_on_switch:
        loaded = await fetch_loaded_models(cfg.ollama_host)
        for m in loaded:
            m_name = m.get("name")
            if m_name and m_name != target_model:
                await unload_model(cfg.ollama_host, m_name)

    full_prompt = _compose_with_history(prompt, history_context)

    async with contextlib.AsyncExitStack() as stack:
        effective_base_url = bridge_base_url or await stack.enter_async_context(
            temporary_bridge_server(cfg.ollama_host)
        )
        agent_cfg = build_specialist_config(
            task_type,
            target_model,
            resolved_cwd,
            cfg,
            effective_base_url,
            browser_mcp=browser_mcp,
        )
        async with Agent(config=agent_cfg) as specialist:
            response = await specialist.chat(full_prompt)
            async for chunk in response.chunks:
                if isinstance(chunk, types.ToolCall):
                    yield {
                        "event": "tool",
                        "name": str(chunk.name),
                        "args": chunk.args,
                    }
                elif isinstance(chunk, types.Thought):
                    if chunk.text:
                        yield {"event": "thought", "text": chunk.text}
                elif isinstance(chunk, types.Text):
                    if chunk.text:
                        yield {"event": "token", "text": chunk.text}

    yield {"event": "done"}

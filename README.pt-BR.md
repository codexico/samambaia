<p align="center">
  <img src="assets/samambaia-header.png" width="280" alt="Samambaia-de-metro no xaxim" />
</p>

<h1 align="center">samambAIA (<code>samambaia</code> / <code>aaaia</code>)</h1>

<p align="center">
  <em>"De cada ferramenta e modelo segundo sua capacidade, a cada tarefa segundo sua necessidade."</em><br/>
  <strong>🇺🇸 <a href="README.md">Read the documentation in English</a></strong>
</p>

---

**Samambaia** é um coordenador de agentes de IA **100% local** para Linux.

Assim como uma samambaia na natureza — que vive em apoio mútuo apoiada nos galhos de árvores maiores sem parasitá-las nem tentar substituí-las — o **Samambaia** orquestra ferramentas livres consagradas ([Podman](https://podman.io/), [Ollama](https://ollama.com/), [Google Antigravity Python SDK](https://github.com/google-antigravity/antigravity-sdk-python), [Open WebUI](https://github.com/open-webui/open-webui), [FastAPI](https://fastapi.tiangolo.com/) e [systemd](https://systemd.io/)) para que máquinas com pouca VRAM e uma boa quantidade de RAM (ex.: **6 GB de VRAM + 32+ GB de RAM**) entreguem fluxos agênticos rápidos e seguros.

## ⚙️ Configuração Padrão (100% Personalizável)

### Modelos padrão e por que foram escolhidos

Em máquinas com VRAM limitada e bastante RAM no sistema, modelos densos grandes (30B+) ficam lentos porque a CPU precisa ler todos os pesos da RAM a cada token gerado. Por isso, a configuração padrão combina arquiteturas **PLE (*Per-Layer Embeddings*)** e **MoE (*Mixture-of-Experts*)**, que entregam inteligência de modelos grandes ativando apenas **3B a 4,5B de parâmetros por token** (5× a 8× mais rápidos):

| Perfil | Modelo Padrão | Ativos / Token | Motivo da escolha |
| :--- | :--- | :--- | :--- |
| **`router` & `direct`** | [`gemma4:e4b`](https://ollama.com/library/gemma4) | ~4,5B (PLE) | Classifica rotas em milissegundos e faz traduções/respostas diretas imediatas (`think=False`) cabendo em pouca VRAM enquanto tabelas PLE ficam na RAM. |
| **`terminal` & `pr_review`** | [`gemma4:26b`](https://ollama.com/library/gemma4) | 4B (MoE) | Excelente estabilidade em *tool calling* nativo e leitura de arquivos/diffs com velocidade de modelo leve e sem atraso de *thinking* longo. |
| **`coding` & `deep`** | [`qwen3.5:35b-a3b`](https://ollama.com/library/qwen3.5) | 3B (MoE + Thinking) | Raciocínio estendido (*thinking*) de ponta em código e arquitetura mantendo ~14–18 tokens/s na RAM por ativar apenas 3B parâmetros por token. |

> Todos os modelos são configuráveis: se você preferir modelos menores, maiores ou de outras famílias no [Ollama](https://ollama.com/search), basta alterar `model_preferences`.

### Outras configurações que podem ser alteradas

Rode `aaaia --init-config` para gerar seu arquivo pessoal em `~/.config/samambaia/config.json` (baseado em [`config.example.json`](config.example.json)) e `aaaia --config` para inspecionar a configuração ativa:

- **`model_preferences`:** lista ordenada de modelos para cada perfil (`router`, `direct`, `terminal`, `pr_review`, `coding` e `deep`).
- **`inference`:** troca/descarregamento automático de VRAM (`unload_other_models_on_switch` — desative se tiver GPU com 24 GB+ de VRAM), tempo em memória (`router_keep_alive`), janelas de contexto (`router_num_ctx`, `direct_num_ctx`), temperatura, limite de compactação de histórico (`compaction_tokens`) e timeouts.
- **`guard`:** quais perfis sempre exibem aviso de confirmação (`always_confirm_tiers`, padrão `["deep"]`), padrões de nomes de modelos pesados (`heavy_model_patterns`, ex.: `35b`, `32b`) e lista de padrões bloqueados em comandos de terminal (`dangerous_command_patterns`).
- **`limits`:** máximo de turnos salvos por sessão (`max_session_turns`), tamanho máximo de contexto histórico, truncamento seguro de `git diff`, PRs e páginas web, e quantidade padrão de resultados de busca.
- **`features`:** ativar/desativar ferramentas de web (`enable_web_tools`), revisão remota do GitHub (`enable_github_pr_tools`) e argumentos customizados dos servidores MCP de Chrome e Firefox.
- **Conexões, Idioma e Desktop:** `ollama_host`, `openwebui_url`, `public_webui_url`, `daemon_host`, `daemon_port`, `default_language`, nomes dos containers Podman (`ollama_container_name`, `openwebui_container_name`) e atalho global do KDE (`kde_selection_shortcut`, padrão `Meta+Alt+T`). Também aceita variáveis de ambiente (`SAMAMBAIA_OLLAMA_HOST`, `SAMAMBAIA_DAEMON_PORT`, `SAMAMBAIA_LANGUAGE`, `SAMAMBAIA_SHORTCUT`).
- **`zettelkasten` *(opcional, desativado por padrão)*:** `enabled` (`false`), pasta de notas (`directory`), arquivo de tarefas (`todo_file`), nome da coleção no Open WebUI (`openwebui_collection_name`) e intervalo de sincronização.

## ✨ Principais Recursos

- **Roteamento em 2 Estágios (Consciente de VRAM):**
  - **Estágio 1 (`router` & `direct` → `gemma4:e4b`):** Classifica o pedido em milissegundos via gramática JSON nativa do [Ollama](https://ollama.com/) e faz streaming imediato de traduções/respostas rápidas (`think=False`, sem overhead de subprocesso).
  - **Estágio 2 (`terminal`, `pr_review`, `coding`, `deep`):** Descarrega automaticamente modelos ociosos antes de subir especialistas maiores (evitando disputa por VRAM) e executa um `Agent` do [Google Antigravity SDK](https://github.com/google-antigravity/antigravity-sdk-python) restrito à pasta atual (`$PWD`) com políticas de segurança declarativas.
- **Aviso Interativo para Tarefas Pesadas & Fila "Deixar para Depois":**
  - Sempre que uma tarefa cai no perfil `deep`, exige carregar a frio o modelo de ~21 GB (`qwen3.5:35b-a3b`) ou envolve múltiplos passos longos, o Samambaia avisa **antes** de alocar RAM/VRAM: **`[C]` Continuar**, **`[D]` Deixar para depois** ou **`[X]` Cancelar** — tanto no terminal quanto nos balões de chat do [Open WebUI](https://github.com/open-webui/open-webui)!
- **Ferramentas Integradas:**
  - **Busca na Web & Leitura Limpa de URLs:** `web_search` ([`ddgs`](https://pypi.org/project/ddgs/) / DuckDuckGo) e `read_webpage` ([BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/)).
  - **Code Review Somente-Leitura (Local e GitHub Remoto):** analisa `git_diff`, `git_status`, `git_log` locais ou PRs remotos pelo número (`gh_pr_view`, `gh_pr_diff` via [GitHub CLI](https://cli.github.com/) ou API REST) sem trocar sua branch nem liberar escrita ou shell arbitrário.
  - **MCP de Navegador sob Demanda:** conecte [`chrome-devtools-mcp`](https://www.npmjs.com/package/chrome-devtools-mcp) (Chrome/Chromium) ou [`@playwright/mcp`](https://www.npmjs.com/package/@playwright/mcp) (Firefox) via `--browser-mcp`.
  - **[Zettelkasten](https://zettelkasten.de/) *(opcional)*:** ativando `"zettelkasten": {"enabled": true}`, habilita ferramentas de notas Markdown (`search_zk`, `read_zk_note`, `append_zk_todo`) e um timer `systemd --user` (0 MB de RAM ocioso) que espelha incrementalmente suas notas na coleção `#zk` do Open WebUI.
- **Integração Nativa com Linux / [KDE Plasma](https://kde.org/plasma-desktop/):**
  - Inicia no login via [systemd](https://systemd.io/) (`systemd --user`) subindo seus containers `ollama` e `open-webui`.
  - Lançador de aplicativo no menu do sistema (com ícone minimalista [`assets/samambaia-icon.png`](assets/samambaia-icon.png)) e atalho global no KDE (**`Super + Alt + T`**) para traduzir ou explicar qualquer texto selecionado na tela. *Aceitamos PRs para outros ambientes!*

## 🚀 Instalação

Pré-requisitos no Linux: Python 3.11+, [`uv`](https://docs.astral.sh/uv/), [Podman](https://podman.io/) e [Ollama](https://ollama.com/) (veja exemplos de criação dos containers `ollama` e `open-webui` no [`README.md`](README.md#1-prerequisites)).

```bash
git clone https://github.com/codexico/samambaia.git
cd samambaia
uv sync
bash scripts/install.sh
```

Configurações pessoais ficam em `~/.config/samambaia/config.json` (`aaaia --init-config`), mantendo o repositório limpo de caminhos ou preferências locais. *(Para quem usa Zettelkasten, passe `bash scripts/install.sh --with-zk` ou ative `zettelkasten.enabled` no config).*

## 💻 Uso no Terminal (`samambaia` ou `aaaia`)

Por que **`aaaia`**? Para digitar mais rápido (`aa` + `TAB` ou `sama` + `TAB`, se fosse `sam` ou `smb` daria conflito com o samba). Tire as consoantes de `s-a-m-a-m-b-a-i-a` e sobram as vogais: **`a-a-a-i-a`**!

```bash
# Status do daemon, Open WebUI, VRAM e fila pendente
aaaia --status

# Gerar e visualizar sua configuração personalizada
aaaia --init-config
aaaia --config

# Tradução instantânea ('direct' -> gemma4:e4b)
aaaia "Traduza para pt-BR: Zero-copy serialization reduces memory bandwidth pressure."

# Conversa multi-turno lembrando do contexto na pasta atual
aaaia "Explique o que o pyproject.toml deste projeto configura"
aaaia -c "E quais bibliotecas de web usamos?"
aaaia --chat   # Chat contínuo no terminal (/new, /later, /exit)

# Busca na web e leitura de URLs
aaaia "Pesquise na web sobre Fedora Atomic Desktop e traga os links principais"
aaaia "Leia https://ollama.com/library/gemma4 e resuma as variantes disponíveis"

# Code review somente-leitura (local ou PR remoto do GitHub)
aaaia "Faça um code review das alterações atuais desta branch"
aaaia "Revise o PR #42"

# Gerenciar tarefas pesadas salvas para depois
aaaia --later
aaaia --run-later
aaaia --drop-later 1
```

## 🌐 Uso pelo Navegador (Open WebUI & Automação MCP)

### 1. Interface Web Completa ([Open WebUI](https://github.com/open-webui/open-webui))
Abra **`http://samambaia.localhost:8080`** no Firefox ou Chrome (ou aperte a tecla `Super`, digite `samambaia` e tecle `Enter`):

- **Roteamento automático no chat:** com o modelo padrão **`samambaia-auto`** selecionado, você tem histórico completo de conversas longas, upload de arquivos, ditado por voz local (Whisper) e busca na web.
- **Apontar para um projeto local a partir do navegador:** comece a mensagem com `cwd:` para que o agente opere naquela pasta:
  ```text
  cwd:~/projs/meu-app Faça um code review das alterações atuais desta branch
  ```
- **Confirmação interativa no próprio chat:** se o pedido exigir o perfil `deep` (`qwen3.5:35b-a3b`), o Samambaia responde imediatamente com um card de aviso — basta enviar **`c`** para continuar na hora, **`d`** para guardar na fila (`!later` lista a fila no chat) ou **`x`** para cancelar.
- **Perfis diretos no seletor de modelos:** você também pode escolher `samambaia-pr-review`, `samambaia-terminal`, `samambaia-coding`, `samambaia-deep`, `samambaia-browser-firefox` ou `samambaia-browser-chrome`.

### 2. Agente controlando ou inspecionando o Firefox / Chrome (MCP)
Para tarefas em que o próprio agente deve abrir páginas, ler o DOM ou inspecionar o console do navegador:

```bash
# Firefox via Playwright MCP
aaaia --browser-mcp firefox "Abra https://example.com no Firefox e informe o título e os links da página"

# Chrome / Chromium via Chrome DevTools MCP
aaaia --browser-mcp chrome "Inspecione a aba ativa no Chrome e verifique se há erros no console"
```

## 📜 Licença & EULA

- Licenciado sob a **Open Software License v. 3.0 (OSL-3.0)** — veja [`LICENSE`](LICENSE).
- Sujeito ao acordo de usuário final em [`EULA`](EULA): *licenciado exclusivamente para pessoas físicas (natural persons), vedado o uso por pessoas jurídicas, corporações ou empresas comerciais.*

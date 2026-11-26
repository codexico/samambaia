<p align="center">
  <img src="assets/samambaia.svg" width="96" height="96" alt="Logo Samambaia" />
</p>

<h1 align="center">samambAIA (<code>samambaia</code> / <code>aaaia</code>)</h1>

<p align="center">
  <em>"De cada ferramenta e modelo segundo sua capacidade, a cada tarefa segundo sua necessidade."</em><br/>
  <strong>🇺🇸 <a href="README.md">Read the documentation in English</a></strong>
</p>

---

**Samambaia** (escondendo o palíndromo **`AIA`**: *Artificial Inteligência Artificial*, e acompanhada do atalho rápido só com suas vogais em ordem: **`aaaia`**) é um coordenador cooperativo de agentes de IA **100% local** para Linux.

Assim como uma samambaia epífita na natureza — que vive em apoio mútuo apoiada nos galhos de árvores maiores sem parasitá-las nem tentar substituí-las — o **Samambaia** orquestra ferramentas livres consagradas (**Podman**, **Ollama**, **Google Antigravity Python SDK**, **Open WebUI**, **systemd** e seu cofre **Zettelkasten**) para que máquinas com pouca VRAM e bastante RAM (ex.: **6 GB de VRAM + 32–64 GB de RAM**) entreguem fluxos agênticos rápidos e seguros.

## ✨ Principais Recursos

- **Roteamento Cooperativo em 2 Estágios (Consciente de VRAM):**
  - **Estágio 1 (`router` & `direct` → `gemma4:e4b`):** Classifica o pedido em milissegundos via gramática JSON nativa do Ollama e faz streaming imediato de traduções/respostas rápidas (`think=False`, sem overhead de subprocesso).
  - **Estágio 2 (`terminal`, `pr_review`, `coding`, `deep`):** Descarrega automaticamente modelos ociosos antes de subir especialistas maiores (evitando disputa por VRAM) e executa um `Agent` do **Google Antigravity SDK** restrito à pasta atual (`$PWD`) com políticas de segurança declarativas.
- **Aviso Interativo para Tarefas Pesadas & Fila "Deixar para Depois":**
  - Sempre que uma tarefa cai no perfil `deep`, exige carregar a frio o modelo de ~21 GB (`qwen3.5:35b-a3b`) ou envolve múltiplos passos longos, o Samambaia avisa **antes** de alocar RAM/VRAM: **`[C]` Continuar**, **`[D]` Deixar para depois** ou **`[X]` Cancelar** — tanto no terminal quanto nos balões de chat do Open WebUI!
- **Ferramentas Integradas:**
  - **Busca na Web & Leitura Limpa de URLs:** `web_search` (DuckDuckGo) e `read_webpage`.
  - **Code Review Somente-Leitura (Local e GitHub Remoto):** analisa `git_diff`, `git_status`, `git_log` locais ou PRs remotos pelo número (`gh_pr_view`, `gh_pr_diff`) sem trocar sua branch nem liberar escrita ou shell arbitrário.
  - **Zettelkasten (`~/zk`):** ferramentas ao vivo (`search_zk`, `read_zk_note`, `append_zk_todo`) **mais** um timer `systemd --user` (0 MB de RAM ocioso) que espelha incrementalmente suas notas na coleção `#zk` do Open WebUI.
  - **MCP de Navegador sob Demanda:** conecte `chrome-devtools-mcp` (Chrome/Chromium) ou `@playwright/mcp --browser firefox` (Firefox) via `--browser-mcp`.
- **Integração Nativa com Linux / KDE Plasma:**
  - Inicia no login via `systemd --user` subindo seus containers `ollama` e `open-webui`.
  - Lançador de aplicativo no menu do sistema e atalho global no KDE (**`Super + Alt + T`**) para traduzir ou explicar qualquer texto selecionado na tela.

## 🚀 Instalação

```bash
git clone https://github.com/codexico/samambaia.git
cd samambaia
uv sync
bash scripts/install.sh
```

Configurações pessoais ficam em `~/.config/samambaia/config.json` (veja [`config.example.json`](config.example.json)), mantendo o repositório limpo de caminhos ou preferências locais.

## 💻 Uso no Terminal (`samambaia` ou `aaaia`)

Por que **`aaaia`**? Tire as consoantes de `s-a-m-a-m-b-a-i-a` e sobram exatamente as vogais em ordem: **`a-a-a-i-a`**! (Basta digitar `aa` + `TAB` ou `sama` + `TAB`).

```bash
# Status do daemon, Open WebUI, VRAM e fila pendente
aaaia --status

# Tradução instantânea ('direct' -> gemma4:e4b)
aaaia "Traduza para pt-BR: Zero-copy serialization reduces memory bandwidth pressure."

# Conversa multi-turno lembrando do contexto na pasta atual
aaaia "Explique o que o pyproject.toml deste projeto configura"
aaaia -c "E quais bibliotecas de web usamos?"
aaaia --chat   # Chat contínuo no terminal (/new, /later, /exit)

# Busca na web, leitura de URLs e notas Zettelkasten (~/zk)
aaaia "Pesquise na web sobre Fedora Atomic Desktop e traga os links principais"
aaaia "O que tenho anotado sobre DNS no meu zk?"

# Code review somente-leitura (local ou PR remoto do GitHub)
aaaia "Faça um code review das alterações atuais desta branch"
aaaia "Revise o PR #42"

# Gerenciar tarefas pesadas salvas para depois
aaaia --later
aaaia --run-later
aaaia --drop-later 1
```

## 📜 Licença & EULA

- Licenciado sob a **Open Software License v. 3.0 (OSL-3.0)** — veja [`LICENSE`](LICENSE).
- Sujeito ao acordo de usuário final em [`EULA`](EULA): *licenciado exclusivamente para pessoas físicas (natural persons), vedado o uso por pessoas jurídicas, corporações ou empresas comerciais.*

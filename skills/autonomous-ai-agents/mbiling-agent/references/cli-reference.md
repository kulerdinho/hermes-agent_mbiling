# Mbiling CLI Reference

Live sources when anything looks stale: `mbiling --help`, `mbiling <command> --help`,
https://hermes-agent.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
mbiling [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
mbiling chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
mbiling setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
mbiling model                Interactive model/provider picker
mbiling fallback [add|remove|list]  Fallback provider chain
mbiling config [show|edit|get|set|unset|path|env-path|check|migrate]
mbiling login / logout       OAuth sign-in / clear stored auth
mbiling doctor [--fix]       Check dependencies and config
mbiling status [--full]      Component summary (--full: every section)
```

### Tools & Skills

```
mbiling tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

mbiling skills list|browse|search QUERY|inspect ID
mbiling skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
mbiling skills config        Enable/disable skills per platform
mbiling skills check|update|uninstall|publish PATH
mbiling skills tap add REPO  Add a GitHub repo as a skill source
mbiling bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
mbiling mcp add NAME (--url or --command) | remove | list | test NAME
mbiling mcp catalog | install NAME     Curated catalog install
mbiling mcp configure NAME             Toggle tool selection
mbiling mcp-serve                      Run Mbiling as an MCP server (mcp-serve plugin)
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
mbiling gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `mbiling photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/

### Sessions

```
mbiling sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
mbiling cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
mbiling webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
mbiling profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
mbiling profile rename A B | alias NAME | export NAME | import FILE
mbiling profile migrate-identity A B   Retry a completed rename's session/routing identity migration
```

### Credentials & Pools

```
mbiling auth                 Interactive credential manager
mbiling auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
mbiling auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
mbiling desktop / gui        Native desktop app
mbiling dashboard            Web admin panel + embedded chat (--stop / --status)
mbiling proxy                OpenAI-compatible local proxy backed by an OAuth provider
mbiling portal               Quick setup / sign in via Nous Portal
mbiling kanban <verb>        Multi-agent work-queue board
mbiling project              Named multi-folder workspaces (see references/projects-and-folders.md)
mbiling skin list|use|set    Switch/tweak skins (see references/themes.md)
mbiling pets <verb>          Pet mascots (see references/petdex.md)
mbiling memory setup|status|off|reset   Memory provider
mbiling secrets bitwarden|onepassword   External secret stores
mbiling moa                  Mixture-of-Agents slots
mbiling hooks / security / backup / import / checkpoints / console
mbiling logs [-f] [errors]   View agent/error logs
mbiling send                 One-off message through a gateway platform
mbiling pairing / plugins / insights / journey / computer-use
mbiling acp                  ACP server (IDE integration)
mbiling completion bash|zsh|fish
mbiling update / uninstall
```

Plugin- and provider-supplied subcommands (e.g. `mbiling photon setup`) only appear once their plugin is installed/active. Importing from OpenClaw, Claude Code, Codex CLI or Cursor (`mbiling import-agent <agent>`, plus the older `mbiling claw migrate|cleanup`) comes from the `agent-importers` plugin; a fresh `mbiling setup` that finds one of those agents offers to install it.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `mbiling config edit` · [Configuration docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `mbiling tools list` · [Tools reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `mbiling skills browse` · [Skills catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `mbiling model` · [Providers guide](https://hermes-agent.nousresearch.com/docs/integrations/providers) |
| Env variables | `mbiling config env-path` · [Env vars reference](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.mbiling/logs/gateway.log` (or `mbiling logs`) |
| Sessions | `mbiling sessions browse` (reads state.db) |

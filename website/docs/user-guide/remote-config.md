---
sidebar_position: 4
title: "Remote Config"
description: "Serve each profile's config from the config plane instead of config.yaml"
---

# Remote Config

**Remote config mode** replaces `config.yaml` with the config plane: each
profile's config is the document the plane resolves for this agent instance
and that profile. The plane layers it from global, tenant, group, owner, and
agent settings, with the profile's own settings last. Administrators can lock
keys at an upper level, and the agent cannot change a locked key.

Remote config is for fleet deployments, for example Mbiling Cloud. A single
machine with one user should keep `config.yaml`.

## Turning it on

Set the following in the environment, in `~/.mbiling/.env`, or in the managed
`/etc/mbiling/.env` (which wins over the other two). A value in `config.yaml`
cannot select the backend, because the backend is what serves config.

| Variable | Meaning |
|---|---|
| `MBILING_CONFIG_BACKEND=remote` | Use the config plane instead of `config.yaml` (default `file`). |
| `MBILING_CONFIG_INSTANCE_ID` | This agent's id in the plane. Mbiling Cloud sets it; on-prem the operator sets it when registering the agent. Required. |
| `MBILING_CONFIG_REMOTE_URL` | The plane's base URL (default `https://config-config.nousresearch.com`). |

The agent authenticates as itself:

- **Mbiling Cloud**: the Nous access token in the profile's `auth.json`.
- **On-prem**: an OAuth2 client-credentials token from your identity provider,
  configured in `.env` with `GATEWAY_RELAY_IDP_TOKEN_URL`,
  `GATEWAY_RELAY_IDP_CLIENT_ID`, `GATEWAY_RELAY_IDP_CLIENT_SECRET`, and
  optionally `GATEWAY_RELAY_IDP_SCOPE`.

These values, and the `MBILING_CONFIG_*` variables above, must come from
`auth.json`, a `.env` file (the profile's, the project `.env` a Mbiling command
loads, or the managed one), or the process environment. If a `secrets:` source
(1Password, Bitwarden, a command) supplies any of them, Mbiling refuses to
start. The same holds for `MBILING_PORTAL_BASE_URL`, `NOUS_PORTAL_BASE_URL` and
`MBILING_SHARED_AUTH_DIR` (they decide where the Cloud token is read and sent).
A multi-profile process (gateway, dashboard) that serves a profile whose
`secrets:` source supplies one of these names refuses to serve that profile.

A Mbiling process started for another profile (a `mbiling -p <name>` worker, a
profile's cron or bot child) keeps the `MBILING_CONFIG_*` deployment of the
process that started it, so it reads that profile's config from the plane too.
It does not inherit the plane credential: on-prem, put the
`GATEWAY_RELAY_IDP_*` values where every profile reads them (the managed
`/etc/mbiling/.env`) or in each profile's `.env`. Without one, the process
exits with an error rather than read a local file.

## What changes

- **Startup fetches config and fails closed.** Every Mbiling process (gateway,
  dashboard, CLI, terminal children) fetches its profile's config when it
  loads `.env`. If the plane cannot be reached after about 30 seconds of
  retries, or refuses the agent, the process exits with an error. It never
  falls back to a local file or to defaults.
- **No local config file.** `config.yaml` is neither read nor written, and
  there is no local cache. A running process keeps the config it last fetched
  in memory. If the plane becomes unreachable while Mbiling runs, the process
  keeps its in-memory config and retries on the next poll.
- **Changes arrive by polling.** Each process checks for changes every 5
  minutes. A change made in the plane reaches a running agent within one
  interval.
- **Writes go to the profile's own level.** `mbiling config set`, `/model`,
  the dashboard, and every other config writer send only the keys that changed,
  guarded against concurrent edits. If another process changed the profile
  meanwhile, Mbiling re-reads it and applies only its own change on top, never
  undoing the other one. Removing a key removes it from the profile level only:
  if an upper level also sets it, that value still applies, and Mbiling warns
  you.
- **Locked keys are refused.** `mbiling config set` (and any setting changed
  in the TUI or desktop app, including the prompt, reasoning display and
  details-mode settings) on a locked key reports which level locks it and
  changes nothing. When a whole document is saved, locked keys are left
  out, and Mbiling prints a note listing them.
- **Secrets stay out of the plane.** A secret-shaped key such as `api_key`
  accepts only a `${VAR}` reference, never the secret itself. Put the value in
  `.env` or a secret source and set the reference:

  ```bash
  mbiling config set model.api_key '${OPENROUTER_API_KEY}'
  ```

- **`secrets:` comes from the plane** like any other key, so administrators can
  manage and lock secret-source settings centrally.
- **Schema migrations run in memory.** A document written by an older Mbiling is
  migrated when it is read, and the result is not written back. A later write
  from a newer Mbiling keeps the profile's stored schema version unless that
  data is already current, so every later reader still migrates it. These
  in-memory migrations change only the config document: steps that also
  tidy local files (removing a retired section from `SOUL.md`, clearing old
  values from `.env`) do not run in remote mode. For the
  same reason, setting a value that one of those migrations rewrites (for
  example `compression.threshold_tokens: 256000`, an old default, on a profile
  stored at an older schema) is refused with a message naming the key: it would
  be saved and then never read back.
- **Unknown keys** in the fetched document are ignored with a warning, for
  example a key added by a newer Mbiling.

## Managed scope

`/etc/mbiling/config.yaml` (see [Managed Scope](./managed-scope.md)) is
**ignored** in remote mode: config and locks come only from the plane. Mbiling
warns at startup, and `mbiling doctor` flags the file. The managed
`/etc/mbiling/.env` still applies.

## Commands that work on the config file

These commands copy or edit `config.yaml` and are refused in remote mode:

- `mbiling config edit` (use `mbiling config set` instead)
- profile clone, profile rename (the plane keeps a profile's settings under its
  name), and profile distribution install
- `mbiling backup`, `mbiling import`, and snapshot restore
- `mbiling gateway --config <file>`

`mbiling doctor` reports the backend instead of the file: the plane URL, the
profile, the profile level's version, the number of locks, and when the config
was last fetched.

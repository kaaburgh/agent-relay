# Providers and runners

Provider-specific CLI syntax is isolated in adapters. Global configuration selects provider/model/options; secrets stay in existing authenticated CLIs/environment and are never committed.

## Codex writer

Example configuration shape:

```yaml
writer:
  provider: codex
  executable: codex
  model: gpt-5.6-luna
  reasoning_effort: max
  options:
    json_flag: --json
```

`CodexWriterProvider` uses non-interactive `codex exec` JSONL semantics. The task prompt is delivered over stdin rather than argv. The adapter captures durable process metadata, thread ID, final agent-message handoff, exposed token-usage counters and errors.

Provider exit zero is not sufficient. Success additionally requires a clean committed descendant of the frozen baseline. Malformed streams and exit-zero/no-commit fail closed. Obvious quota/rate-limit/unavailable messages become provider-unavailable rather than generic process failure.

## Claude reviewer

Example configuration shape:

```yaml
reviewer:
  provider: claude
  executable: claude
  model: opus-5
  options:
    permission_mode: plan
    tools: Read,Grep,Glob
    max_turns: 20
```

`ClaudeReviewerProvider` launches a fresh non-persistent Claude Code process against a dedicated detached worktree at the exact candidate SHA. It requests strict JSON output under the core review schema, uses `plan` permission mode and rejects write tools. Any change to reviewer HEAD/worktree invalidates the review.

The review package is bounded: task/acceptance criteria, baseline and candidate SHAs, changed paths/bounded diff, deterministic validation/runtime evidence, commands and explicitly untrusted writer claims. Private writer reasoning/history is not forwarded.

## Simulated providers

Use `writer.provider: simulated` and `reviewer.provider: simulated` with a runner explicitly marked `options.simulated_validator: true`. See `examples/config.simulated.yaml` and [simulation.md](simulation.md).

## Local runners

A local runner is configuration metadata for a tool that executes through the normal subprocess supervisor:

```yaml
runners:
  local:
    kind: local
```

External adapters still own their command/evidence contracts.

## SSH external-tool transport

Minimal remote configuration:

```yaml
runners:
  gpu-node:
    kind: ssh
    host: gpu.example.invalid
    user: ubuntu
    base_dir: /srv/agent-relay
    options:
      ssh_args: [-p, "22"]
```

`SSHExternalToolRunner` launches the configured SSH executable using local argv semantics with `-T` and `BatchMode=yes`. Remote argv, cwd and environment values are shell-quoted and supplied to `sh -s` over stdin. Task-controlled command data is therefore not interpolated into a local shell command.

Important trust boundary: `host`, `user`, `base_dir`, executable and `ssh_args` are operator-controlled global configuration. In particular, OpenSSH options can affect local SSH behavior; never copy model/task-controlled data into `ssh_args`.

Current SSH limitations are deliberate:

- command stdin is unavailable because stdin transports the launch script;
- no rsync/SCP/artifact-transfer protocol is implemented;
- remote validation evidence must be on storage visible to the local orchestrator, or an explicit transfer layer must run before local evidence parsing;
- timeout/cancel supervises the local SSH process group; arbitrary remote processes that deliberately daemonize/detach are not guaranteed to die when the connection is terminated;
- there is no remote agent-relay state machine or distributed scheduler.

## Provider health checks

`agent-relay doctor --config ...` checks configured executable availability and can run an optional trusted provider `auth_check_argv` probe. Probe output is not treated as credentials and is bounded; examples should never contain tokens or passwords.

# Security Policy

Do not open public issues containing credentials, tokens, provider session material, private repository contents, or exploitable vulnerability details.

Use GitHub private vulnerability reporting when available, or contact the maintainer through a non-public channel before disclosing sensitive details.

`agent-relay` manages external processes and authenticated CLIs. Security-sensitive changes should preserve these rules:

- never persist or log provider credentials;
- prefer argv-based execution over shell evaluation;
- validate configured repository/runtime paths;
- do not discard unknown user worktree state;
- isolate managed worktrees and process groups;
- treat provider/model output as untrusted structured input.

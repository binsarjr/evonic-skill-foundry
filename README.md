# Skill Foundry for Evonic

Skill Foundry gives Evonic procedural self-improvement: it reviews tool-heavy conversations after the response is delivered, creates reusable native skills, and improves matching skills it generated earlier. Agents can also explicitly inspect, create, and patch their own Foundry skills through `skill_foundry_manage`.

## Behavior

- Reviews after 10 accumulated tool calls per session by default.
- Prefers updating a matching Foundry skill over creating a duplicate.
- Makes approved changes available to the source agent on its next turn.
- Sends a short source-session notification after a background create or update.
- Never modifies manually installed, core, or another agent's skills.
- Does not persist conversation transcripts.
- Blocks secret, prompt-injection, destructive-command, and privilege-escalation patterns.

Automatic review, enablement, assignment, and per-agent participation default on once the plugin itself is enabled. Approval remains available as an optional operator setting.

## Installation

1. Download `skill-foundry-v0.2.0.zip` from GitHub Releases.
2. Upload it from Evonic's Plugins page and enable Skill Foundry.
3. Open **Skill Foundry** in the sidebar to inspect activity and generated changes.
4. Disable `Skill Foundry Self-improvement` on any agent that should opt out.

## Agent tool

`skill_foundry_manage` supports:

- `list` and `view` for skills owned by the calling agent;
- `create` for a new procedure;
- `patch` for one exact text replacement in an existing procedure.

There is intentionally no delete action and no executable support-file generation.

## Lifecycle

With approval enabled: `pending_review -> approved -> materialized -> enabled -> assigned`.

With approval disabled, validated changes materialize immediately. Generated skills start at `0.1.0`; updates retain the same skill ID and increment the patch version.

## Development

```bash
python -m unittest discover -s tests -v
```

## License

MIT

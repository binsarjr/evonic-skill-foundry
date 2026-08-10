# Skill Foundry for Evonic

Skill Foundry reviews completed Evonic conversations asynchronously and turns reusable procedures into native, persistent Evonic knowledge skills. It supports optional approval, automatic global enablement, and automatic assignment to the source agent, all configured through Evonic's native plugin detail page.

## Safety defaults

- Plugin disabled after installation
- Auto-review disabled
- Per-agent auto-review opt-in disabled
- Approval required
- Auto-enable disabled
- Auto-assignment disabled
- Generated v1 skills contain only `skill.json` and `SYSTEM.md`, never executable code
- Secret, prompt-injection, destructive-command, and privilege-escalation patterns are blocked

For Hermes-like next-turn availability, explicitly set:

```text
REQUIRE_APPROVAL=false
AUTO_ENABLE_GENERATED_SKILLS=true
AUTO_ASSIGN_GENERATED_SKILLS=true
```

Auto-assignment requires auto-enable. Invalid combinations are reported and generation is rejected.

## Installation

1. Download `skill-foundry-v1.0.0.zip` from GitHub Releases.
2. In Evonic, open Plugins and upload the ZIP.
3. Enable Skill Foundry.
4. Open its plugin detail page and configure global options.
5. Open each desired agent's detail page and enable `Skill Foundry Auto-review`.

Dashboard: `/skill-foundry`

## Lifecycle

With approval enabled: `pending_review -> approved -> materialized -> enabled -> assigned`.

With approval disabled, validation is followed immediately by materialization. Auto-enable and auto-assign then run according to settings. Native `SkillsManager` and `agent_skills` state are used, so an enabled and assigned generated skill is visible on the source agent's next turn.

## Reviewer model

`REVIEW_MODEL_ID` may name an existing Evonic model. If empty, Skill Foundry uses the source agent's model, then Evonic's default model. Transcript review stays inside Evonic's configured model infrastructure.

## Development

```bash
python -m pytest -q
```

## License

MIT

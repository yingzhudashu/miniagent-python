# Migration to MiniAgent 3.0

MiniAgent 3.0 is the only supported runtime format. It does not automatically
convert, back up, or rewrite older configuration and state files. Make a copy
of your data and migrate it manually before starting 3.0.

## Configuration

Replace the old single-model section with provider, profile, and role entries:

```json
{
  "llm": {
    "providers": {
      "openai": {"driver": "openai", "api_key_env": "OPENAI_API_KEY"}
    },
    "models": {
      "primary": {
        "provider": "openai",
        "model": "gpt-5-mini",
        "api": "openai_responses",
        "defaults": {"temperature": 0.7, "max_tokens": 4096}
      }
    },
    "roles": {
      "default": "primary",
      "fast": "primary",
      "reasoning": "primary",
      "vision": "primary"
    }
  },
  "secrets": {
    "llm": {"openai": {"api_key": "..."}}
  }
}
```

Credentials may instead remain solely in the provider environment variable.
Provider-specific behavior belongs in the selected profile's `compatibility`
object. For Qwen thinking, set `"thinking_adapter": "qwen"` explicitly.

## State files

Every JSON state file must be an object with the exact current
`schema_version`. Documents with a `version` field, no version, a different
version, or an array root are rejected without modification. Create a fresh
3.0 state directory and manually copy the user data you need into documents
written with the current schemas. Session history currently uses schema 2;
other schema numbers are defined by the 3.0 writer that owns each file.

MiniAgent never deletes old `.bak`, session, or workspace files. After you have
verified the new installation, archive or remove old files yourself.

## Embedded Python API

Only these package entry modules are public:

- `miniagent.llm`
- `miniagent.agent`
- `miniagent.ui`
- `miniagent.assistant`

Call models through `LLMGateway` with explicit `role` and optional `profile`.
Construct `AgentServices` with an `LLMGateway`, immutable `AgentSettings`, and
the required ports. Start the product through `run_assistant(argv)`, or compose
it with `create_assistant_application()` and call `AssistantApplication.run()`.

There are no compatibility import modules for pre-3.0 internal paths.

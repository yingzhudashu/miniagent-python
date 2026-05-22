# Skill vetting checklist (compact)

- [ ] Author and repo identity verified; no typosquat.
- [ ] License file present and acceptable for your org.
- [ ] No `eval` / `exec` / hidden `base64` payloads in shipped code.
- [ ] Network destinations are explicit and justified.
- [ ] Filesystem scope matches claimed sandbox.
- [ ] No credential templates or `.env` harvesting language in `SKILL.md`.
- [ ] Dependencies pinned or reviewed (`requirements.txt`, `pyproject.toml`).
- [ ] Risk level assigned; HIGH+ escalated to human.

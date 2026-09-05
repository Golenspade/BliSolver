# Agent entry and discovery

Read this only when setting up a host, troubleshooting discovery, or maintaining the package.
Ordinary video operations start at SKILL.md and the relevant CLI/MCP contract.

## Three integration paths

| Entry | What the agent reads | What the host must do |
|---|---|---|
| Clone + file access | README/AGENTS link → SKILL.md → selected reference | Allow reading the trusted checkout; native skill registration is not required for this path |
| Project skill or installed plugin | Skill name/description → SKILL.md → selected reference | Scan the supported location, expose metadata, and activate the skill when relevant |
| MCP with resource access | Short server/tool instructions → guidance resource → selected reference | Expose resources/list and resources/read to the agent or attach their results |

A clone is a filesystem operation. Reading README is not an installation API. MCP discovery lists
server capabilities; it does not mandate local SKILL.md discovery or host-side activation.
Verify the actual host catalog; do not claim a skill was registered just because its file exists.

## Single-source directory layout

```text
<repository-root>/
├── README.md / README_zh.md  # visible entry, link to canonical SKILL.md
├── AGENTS.md                # repository operation trigger plus development rules
├── .agents/skills/
│   └── blisolver-video-ingestion -> ../../skills/blisolver-video-ingestion
├── plugin.json / mcp.json   # Agent Plugins package and stdio server
├── bin/blisolver-mcp
├── blisolver/               # application, needed by the skill scripts
└── skills/blisolver-video-ingestion/
    ├── SKILL.md             # short shared workflow and task routing
    ├── scripts/             # execute helpers without preloading implementation
    └── references/          # one selected topic at a time
```

The relative directory symlink stays inside the repository and resolves to the canonical skill.
Both discovery locations therefore expose the same name, metadata, instructions and scripts.
A host that scans both should deduplicate by resolved path/name; do not install two copies.

Agent Plugins uses the canonical `skills/<name>/SKILL.md`. `.agents/skills/` is a cross-client
project convention, not a requirement of the Agent Skills file format. Hosts differ in scanning,
trust, symlink support, and when they refresh. Start/reload a session rooted in the checkout if the
catalog is stale. On systems where Git checks out symlinks as text, or hosts that reject symlinks,
read the README's canonical link or install the whole plugin using the host's supported workflow.
Do not copy just SKILL.md into a user directory: its scripts need the colocated application.
There is no universal install or refresh command to invent.

## Context loading policy

- Discovery: only skill metadata or the short README/server pointer.
- Activation: one SKILL.md with shared sequence and boundaries.
- Operation: read CLI **or** MCP contract; read setup/provider/stage details when that decision arises.
- Handoff: read the envelope/schema section and inspect the actual result, without bulk-loading media
  or all transcript bodies merely to validate paths.

References are linked directly from SKILL.md. Updating one does not require duplicating it in
README, AGENTS, another skill, or server instructions. A host may make different context choices;
the package supplies this route but cannot force every agent to follow it.

## MCP resource bridge

Server instructions and the probe/start tool descriptions point to `blisolver://guidance/SKILL.md`.
`resources/list` advertises metadata; `resources/read` returns one selected document. Every
`references/<name>.md` is available at `blisolver://guidance/references/<name>.md`. These documents
are the same files used by local Skill activation, not separate MCP manuals.

`blisolver://runtime/doctor.json` returns the server environment's offline doctor report. It can
create configured cache/output directories and write/delete a local writability probe; it does
not fetch videos, load external models, or extract browser cookies. Treat a stage warning as an
unmet prerequisite even if the overall report is not an error.

Resource access is a host capability. If it is hidden, use local Skill files when available or
have the host expose/attach the required resource. No undocumented `read_skill` tool exists. A
Python wheel alone is not the complete Agent Plugin; missing guidance must be diagnosed and the
complete matching checkout/plugin supplied, rather than fabricating operating instructions.

## Standards audit — 2026-09-05

- [Agent Skills format](https://agentskills.io/specification): uppercase `SKILL.md`, YAML metadata,
  matching directory/name, optional scripts/references, and progressive disclosure. It does not
  specify a date-versioned MCP-style release identifier or mandate installation directories.
- [Agent Skills host integration](https://agentskills.io/client-implementation/adding-skills-support):
  discovery, metadata catalog, activation, `.agents/skills/` convention and host trust behavior.
- [Agent Plugins 1.0.0](https://agent-plugins.org/specification): root manifests and fixed component
  locations; install/enablement remains client-defined. Contained symlinks are permitted.
- [Latest MCP](https://modelcontextprotocol.io/specification/latest) resolves to `2026-07-28`;
  [Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) is the
  published context-reading mechanism used here, with host-controlled inclusion.
- [Skills Over MCP working group](https://modelcontextprotocol.io/community/working-groups/skills-over-mcp)
  points to [SEP-2640](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2640), still
  open/in review at this audit. This repository does not claim that draft extension or reserve its
  `skill://` namespace. Its `blisolver://` resources use the core MCP resource API.

The stdio server retains legacy `2025-11-25` compatibility. These discovery improvements do not
change the video pipeline, tool arguments, or job handle contract.

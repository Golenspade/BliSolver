# Install this skill in your agents

## Easy install

Copy this single sentence into your agent:

```text
Install the `blisolver-video-ingestion` skill from `https://github.com/alttina/bilibili-get-content` for all supported agents using `npx skills add https://github.com/alttina/bilibili-get-content --skill blisolver-video-ingestion --global --agent '*' --yes`; then verify with `npx skills list` and confirm that `blisolver-video-ingestion` is listed.
```

## Terminal command

Copy and run this single command in a terminal to install the `blisolver-video-ingestion` skill for
all supported agents:

```bash
npx skills add alttina/bilibili-get-content --skill blisolver-video-ingestion --global --agent '*' --yes
```

The repository name is `bilibili-get-content`; the installed skill name is
`blisolver-video-ingestion`.

For one specific agent, replace `'*'` with its adapter name, for example:

```bash
npx skills add alttina/bilibili-get-content --skill blisolver-video-ingestion --global --agent codex --yes
```

For a project-local installation, omit `--global`:

```bash
npx skills add alttina/bilibili-get-content --skill blisolver-video-ingestion --yes
```

Verify installation with:

```bash
npx skills list
```

This installs the Agent Skill instructions and helper scripts. It does not install the BliSolver
application, Python environments, ffmpeg, Whisper models, LM Studio, browser cookies, or media
files. Point the installed wrappers at a BliSolver checkout with `--project-root` or
`BLISOLVER_PROJECT_ROOT`.

"""Agent Plugins 1.0.0 package conformance for this repository.

The repository root IS the plugin root: `plugin.json`, `mcp.json`, and `skills/` sit beside the
`blisolver/` application instead of shadowing it in a separate copied package. That is the
structural fix for the drift class this suite guards — a skill cannot fall behind an
implementation it lives next to and imports.

Schemas are vendored under `tests/fixtures/agent-plugins/1.0.0/`. Agent Plugins §5.2 and §7.2.1
forbid clients from retrieving a schema while loading a plugin, so the offline copies here mirror
that discipline and keep the suite network-free.

Spec references are to https://agent-plugins.org/specification (v1.0.0, Working Draft).
Section 11 specifies client behavior; BliSolver is a plugin package, not a plugin client, so that
section is intentionally outside this suite's conformance claim.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = Path(__file__).resolve().parent / "fixtures" / "agent-plugins" / "1.0.0"
SPEC_VERSION = "1.0.0"
SCHEMA_BASE = f"https://agent-plugins.org/schemas/{SPEC_VERSION}/"

# §5.5 plugin name; the schema encodes the same rule but a lookahead is easy to get wrong, so the
# constraint is asserted directly as well.
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
# Agent Skills `name`: lowercase alphanumerics and hyphens only, no leading/trailing/double hyphen.
SKILL_NAME_RE = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

# §7.2.1 `cwd` must be plugin-relative or rooted at one of the two reserved placeholders.
CWD_RE = re.compile(r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))")
RESERVED_ENV = {"PLUGIN_ROOT", "PLUGIN_DATA"}

# Substrings that must never appear as a literal `env`/`headers` key in package data (§9.2: "not a
# portable secret mechanism"). Deliberately narrow — this catches a credential being pasted into
# mcp.json, not every conceivable naming style.
SECRET_KEY_HINTS = ("SESSDATA", "API_KEY", "TOKEN", "PASSWORD", "SECRET", "COOKIE")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(_load(SCHEMA_DIR / schema_name))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _load(PLUGIN_ROOT / "plugin.json")


@pytest.fixture(scope="module")
def mcp_config() -> dict:
    return _load(PLUGIN_ROOT / "mcp.json")


# --- manifest (§5) -------------------------------------------------------------------------


def test_manifest_exists_at_plugin_root():
    """§5.1: clients check for a manifest at `plugin.json` in the plugin root."""
    path = PLUGIN_ROOT / "plugin.json"
    assert path.is_file()
    assert path.resolve().is_relative_to(PLUGIN_ROOT.resolve())


def test_manifest_matches_canonical_schema(manifest):
    """§5.2: the manifest schema is closed; any violation other than an unknown top-level field
    or a non-object `extensions` is fatal to the plugin. We allow neither."""
    errors = sorted(_validator("plugin.schema.json").iter_errors(manifest), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)


def test_manifest_declares_the_supported_spec_version(manifest):
    """§5.2: `$schema` MUST be the canonical identifier for the targeted Agent Plugins version."""
    assert manifest["$schema"] == f"{SCHEMA_BASE}plugin.schema.json"


def test_plugin_name_satisfies_name_constraints(manifest):
    """§5.5: 1-64 chars, lowercase alphanumerics/hyphens/periods, alphanumeric ends, no `--`/`..`."""
    name = manifest["name"]
    assert 1 <= len(name) <= 64
    assert PLUGIN_NAME_RE.match(name), f"invalid plugin name: {name!r}"


def test_release_version_is_semver_and_stays_in_sync(manifest):
    """§10.2 recommends SemVer; the plugin and Python package are one release artifact."""
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    init_text = (PLUGIN_ROOT / "blisolver" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
    assert match, "blisolver.__version__ is not declared"

    version = manifest["version"]
    semver = (
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
        r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
    )
    assert re.fullmatch(semver, version), f"plugin version is not SemVer: {version!r}"
    assert version == project["project"]["version"] == match.group(1)


# --- MCP configuration (§7.2) --------------------------------------------------------------


def test_mcp_config_exists_at_plugin_root():
    """§6.2: MCP discovery uses the fixed root path `mcp.json`."""
    path = PLUGIN_ROOT / "mcp.json"
    assert path.is_file()
    assert path.resolve().is_relative_to(PLUGIN_ROOT.resolve())


def test_mcp_config_matches_canonical_schema(mcp_config):
    """§7.2.1: `mcp.json` carries only `$schema` and `mcpServers`, and each entry matches exactly
    one closed transport variant."""
    errors = sorted(_validator("mcp.schema.json").iter_errors(mcp_config), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)


def test_mcp_spec_version_matches_the_manifest(manifest, mcp_config):
    """§10.1: a version mismatch between `plugin.json` and `mcp.json` invalidates MCP for the
    whole plugin. This is the single easiest way to silently lose every tool."""
    assert mcp_config["$schema"] == f"{SCHEMA_BASE}mcp.schema.json"
    manifest_version = manifest["$schema"].rsplit("/", 2)[-2]
    mcp_version = mcp_config["$schema"].rsplit("/", 2)[-2]
    assert manifest_version == mcp_version == SPEC_VERSION


def _stdio_servers(mcp_config: dict) -> list[tuple[str, dict]]:
    return [
        (name, entry)
        for name, entry in mcp_config["mcpServers"].items()
        if entry.get("type") == "stdio"
    ]


def test_at_least_one_transport_is_configured(mcp_config):
    """A plugin with an empty server map is valid per spec but pointless here: the MCP surface is
    a deliverable of this package."""
    assert mcp_config["mcpServers"], "no MCP servers configured"


def test_stdio_commands_are_single_resolvable_executable_tokens(mcp_config):
    """§7.2.1: `command` is one token, either a bare name or a plugin-relative `./` path, and gets
    no placeholder expansion. A bundled executable MUST use the plugin-relative form."""
    servers = _stdio_servers(mcp_config)
    assert servers, "expected at least one stdio server"
    for name, entry in servers:
        command = entry["command"]
        assert command.strip() == command, f"{name}: command has surrounding whitespace"
        assert " " not in command, f"{name}: command must be one token, not a shell string"
        assert "${" not in command, f"{name}: placeholders are not expanded in command"
        if not command.startswith("./"):
            continue  # bare name: platform search, nothing local to verify
        resolved = (PLUGIN_ROOT / command).resolve()
        assert resolved.is_relative_to(PLUGIN_ROOT.resolve()), f"{name}: escapes the plugin root"
        assert resolved.is_file(), f"{name}: bundled command missing: {command}"
        assert os.access(resolved, os.X_OK), f"{name}: bundled command not executable: {command}"


def test_stdio_cwd_uses_a_permitted_form(mcp_config, tmp_path):
    """§7.2.1: an explicit `cwd` is `./`-relative, `${PLUGIN_ROOT}`-rooted, or
    `${PLUGIN_DATA}`-rooted, and must stay inside the corresponding directory."""
    plugin_root = PLUGIN_ROOT.resolve()
    plugin_data = (tmp_path / "plugin-data").resolve()
    plugin_data.mkdir()

    for name, entry in _stdio_servers(mcp_config):
        cwd = entry.get("cwd")
        if cwd is None:
            continue  # defaults to the plugin root
        assert CWD_RE.match(cwd), f"{name}: cwd form not permitted: {cwd!r}"
        if cwd.startswith("./"):
            base, relative = plugin_root, cwd[2:]
        elif cwd.startswith("${PLUGIN_ROOT}"):
            base, relative = plugin_root, cwd.removeprefix("${PLUGIN_ROOT}").lstrip("/")
        else:
            base, relative = plugin_data, cwd.removeprefix("${PLUGIN_DATA}").lstrip("/")
        resolved = (base / relative).resolve()
        assert resolved.is_relative_to(base), f"{name}: cwd escapes its declared root"


def test_stdio_env_does_not_shadow_reserved_variables(mcp_config):
    """§9.2: an `env` entry named PLUGIN_ROOT or PLUGIN_DATA invalidates the server entry — the
    client supplies both itself."""
    for name, entry in _stdio_servers(mcp_config):
        assert not (RESERVED_ENV & set(entry.get("env") or {})), f"{name}: shadows a reserved var"


def test_package_data_carries_no_credentials(mcp_config):
    """§9.2 / §7.2.1: configured `env` values and HTTP `headers` are visible package data and MUST
    NOT carry secrets. mcp.json is committed, so this is a real leak path."""
    for name, entry in mcp_config["mcpServers"].items():
        for field in ("env", "headers"):
            for key in entry.get(field) or {}:
                upper = key.upper()
                assert not any(h in upper for h in SECRET_KEY_HINTS), (
                    f"{name}.{field}: {key!r} looks like a credential; keep it out of the package"
                )


def test_package_uses_only_portable_placeholders(mcp_config):
    """§9.2 expands only `${PLUGIN_ROOT}` and `${PLUGIN_DATA}`. BliSolver deliberately avoids
    leaving any other placeholder as literal subprocess text."""
    allowed = {"PLUGIN_ROOT", "PLUGIN_DATA"}
    for name, entry in _stdio_servers(mcp_config):
        values = [*(entry.get("args") or []), *(entry.get("env") or {}).values()]
        if entry.get("cwd"):
            values.append(entry["cwd"])
        for value in values:
            for found in re.findall(r"\$\{([^}]*)\}", value):
                assert found in allowed, f"{name}: unrecognized placeholder ${{{found}}}"


# --- skills (§6.1, §7.1) -------------------------------------------------------------------


def _discovered_skills() -> list[Path]:
    """§7.1: exactly the immediate children of `skills/` whose `SKILL.md` is a regular file."""
    skills_dir = PLUGIN_ROOT / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(c for c in skills_dir.iterdir() if c.is_dir() and (c / "SKILL.md").is_file())


def test_skills_live_in_the_fixed_location():
    """§6.1: the fixed discovery location is `skills/` and cannot be overridden by plugin.json."""
    assert (PLUGIN_ROOT / "skills").is_dir()
    assert _discovered_skills(), "no discoverable skill under skills/"


def test_no_skill_is_hidden_below_the_discovery_depth():
    """§7.1: clients MUST NOT recurse past immediate children. A deeper SKILL.md would be silently
    ignored, which is the worst kind of packaging bug — it looks present and never loads."""
    skills_dir = PLUGIN_ROOT / "skills"
    too_deep = [
        p.relative_to(PLUGIN_ROOT)
        for p in skills_dir.rglob("SKILL.md")
        if p.is_file() and p.parent.parent != skills_dir
    ]
    assert not too_deep, f"SKILL.md below discovery depth (never loaded): {too_deep}"


@pytest.mark.parametrize("skill_dir", _discovered_skills(), ids=lambda p: p.name)
def test_skill_frontmatter_conforms_to_agent_skills(skill_dir: Path):
    """Agent Skills specification: `name` and `description` are required, `name` must match the
    parent directory, and the optional fields have hard length/type limits."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{skill_dir.name}: SKILL.md must open with YAML frontmatter"
    _, raw, body = text.split("---", 2)
    front = yaml.safe_load(raw)
    assert isinstance(front, dict), f"{skill_dir.name}: frontmatter must be a mapping"
    assert body.strip(), f"{skill_dir.name}: SKILL.md must contain Markdown instructions"

    name = front.get("name")
    assert isinstance(name, str) and name, f"{skill_dir.name}: `name` is required"
    assert len(name) <= 64, f"{skill_dir.name}: `name` exceeds 64 characters"
    assert SKILL_NAME_RE.match(name), f"{skill_dir.name}: invalid skill name {name!r}"
    assert name == skill_dir.name, f"`name` ({name!r}) must match directory ({skill_dir.name!r})"

    description = front.get("description")
    assert isinstance(description, str) and description.strip(), "`description` is required"
    assert len(description) <= 1024, f"{skill_dir.name}: `description` exceeds 1024 characters"

    if "compatibility" in front:
        compatibility = front["compatibility"]
        assert isinstance(compatibility, str) and 1 <= len(compatibility) <= 500

    if "license" in front:
        assert isinstance(front["license"], str), f"{skill_dir.name}: `license` must be a string"

    if "allowed-tools" in front:
        assert isinstance(front["allowed-tools"], str), (
            f"{skill_dir.name}: `allowed-tools` must be a space-delimited string"
        )

    if "metadata" in front:
        metadata = front["metadata"]
        assert isinstance(metadata, dict), f"{skill_dir.name}: `metadata` must be a mapping"
        for key, value in metadata.items():
            assert isinstance(key, str) and isinstance(value, str), (
                f"{skill_dir.name}: metadata must map strings to strings; "
                f"{key!r} -> {value!r} ({type(value).__name__})"
            )

    unknown = set(front) - {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
    assert not unknown, f"{skill_dir.name}: unknown frontmatter fields: {sorted(unknown)}"


@pytest.mark.parametrize("skill_dir", _discovered_skills(), ids=lambda p: p.name)
def test_skill_stays_inside_the_plugin_root(skill_dir: Path):
    """§4.1: every package path a client reads MUST resolve within the plugin root. Symlinks may
    point inside it but never out."""
    root = PLUGIN_ROOT.resolve()
    for path in skill_dir.rglob("*"):
        assert path.resolve().is_relative_to(root), f"escapes the plugin root: {path}"

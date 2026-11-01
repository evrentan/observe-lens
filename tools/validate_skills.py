#!/usr/bin/env python3
"""Validate skill folders: frontmatter name/description and that every
backticked scripts/, references/ or assets/ path in SKILL.md exists.
Also validates the plugin manifests (Claude Code, Devin, Cursor, Codex)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

PLUGIN_FILES = [
    ROOT / "plugin.json",
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / ".cursor-plugin" / "plugin.json",
    ROOT / ".cursor-plugin" / "marketplace.json",
    ROOT / ".codex-plugin" / "plugin.json",
    ROOT / ".agents" / "plugins" / "marketplace.json",
]
VERSIONED_MANIFESTS = [".claude-plugin/plugin.json",
                       ".cursor-plugin/plugin.json",
                       ".codex-plugin/plugin.json"]
PLUGIN_TOP_KEYS = {"$schema", "name", "version", "description", "author",
                   "homepage", "repository", "license", "keywords",
                   "extensions"}
PLUGIN_SCHEMA_URL = ("https://agent-plugins.org/schemas/1.0.0/"
                     "plugin.schema.json")
PLUGIN_NAME_RE = re.compile(
    r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


def _parse_frontmatter(text):
    if not text.startswith("---"):
        return None, "no frontmatter"
    end = text.find("\n---", 3)
    if end < 0:
        return None, "unterminated frontmatter"
    fm = {"_raw": text[3:end]}
    cur_key = None
    for line in text[3:end].splitlines():
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if m:
            cur_key = m.group(1)
            val = m.group(2).strip()
            if val in (">-", ">", "|", "|-"):
                fm[cur_key] = ""
            else:
                fm[cur_key] = val.strip('"').strip("'")
        elif cur_key and line.startswith("  "):
            fm[cur_key] = (fm.get(cur_key, "") + " " + line.strip()).strip()
    return fm, None


def _plugin_names_ok(f, obj, problems):
    names = [obj.get("name")]
    for p in obj.get("plugins") or []:
        names.append(p.get("name"))
    for n in names:
        if n != "observe-lens":
            problems.append("%s: name %r != 'observe-lens'" % (f, n))


def _plugin_schema_ok(root, problems):
    extra = set(root) - PLUGIN_TOP_KEYS
    if extra:
        problems.append("plugin.json: unexpected keys %s" % sorted(extra))
    if root.get("$schema") != PLUGIN_SCHEMA_URL:
        problems.append("plugin.json: $schema must be %s"
                        % PLUGIN_SCHEMA_URL)
    if not PLUGIN_NAME_RE.match(root.get("name") or ""):
        problems.append("plugin.json: name %r fails the Agent Plugins "
                        "name format" % root.get("name"))


def _plugin_versions_ok(parsed, skill_versions, problems):
    versions = []
    root = parsed.get(ROOT / "plugin.json") or {}
    versions.append(("plugin.json", root.get("version")))
    for rel in VERSIONED_MANIFESTS:
        versions.append((rel, (parsed.get(ROOT / rel) or {}).get("version")))
    cursor = parsed.get(ROOT / ".cursor-plugin" / "marketplace.json") or {}
    versions.append((".cursor-plugin metadata",
                     (cursor.get("metadata") or {}).get("version")))
    for p in cursor.get("plugins") or []:
        versions.append((".cursor-plugin plugin entry", p.get("version")))
    for label, v in versions:
        if skill_versions and v not in skill_versions:
            problems.append("%s: version %r != skills' metadata.version %s"
                            % (label, v, sorted(skill_versions)))


def _load_plugin_files(problems):
    parsed = {}
    for f in PLUGIN_FILES:
        if not f.exists():
            problems.append("missing plugin manifest %s" % f)
            continue
        try:
            parsed[f] = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append("%s: invalid JSON: %s" % (f, e))
    return parsed


def validate_plugins(problems, skill_versions):
    parsed = _load_plugin_files(problems)
    for f, obj in parsed.items():
        _plugin_names_ok(f, obj, problems)
    _plugin_schema_ok(parsed.get(ROOT / "plugin.json") or {}, problems)
    _plugin_versions_ok(parsed, skill_versions, problems)


def _validate_skill(skill_dir, problems, skill_versions):
    md = skill_dir / "SKILL.md"
    if not md.exists():
        problems.append("%s: missing SKILL.md" % skill_dir.name)
        return
    text = md.read_text(encoding="utf-8")
    fm, err = _parse_frontmatter(text)
    if err:
        problems.append("%s: %s" % (skill_dir.name, err))
        return
    if fm.get("name") != skill_dir.name:
        problems.append("%s: frontmatter name '%s' != folder name"
                        % (skill_dir.name, fm.get("name")))
    desc = fm.get("description", "")
    if not desc:
        problems.append("%s: empty description" % skill_dir.name)
    if len(desc) > 1024:
        problems.append("%s: description %d chars > 1024"
                        % (skill_dir.name, len(desc)))
    if "<" in desc or ">" in desc:
        problems.append("%s: description contains angle brackets"
                        % skill_dir.name)
    for m in re.finditer(r"`((?:scripts|references|assets)/[^`\s]+)`",
                         text):
        rel = m.group(1)
        if not (skill_dir / rel).exists():
            problems.append("%s: missing %s" % (skill_dir.name, rel))
    mv = re.search(r"^\s+version:\s*\"?([^\"\n]+?)\"?\s*$",
                   fm.get("_raw", ""), re.M)
    if mv:
        skill_versions.add(mv.group(1))


def main():
    problems = []
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    if not skills:
        problems.append("no skills found under %s" % SKILLS)
    skill_versions = set()
    for skill_dir in skills:
        _validate_skill(skill_dir, problems, skill_versions)
    validate_plugins(problems, skill_versions)
    if problems:
        for p in problems:
            print(p)
        sys.exit(1)
    print("all %d skills valid" % len(skills))


if __name__ == "__main__":
    main()

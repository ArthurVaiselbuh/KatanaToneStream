# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The canonical codebase documentation lives in **AGENTS.md** — read that file for architecture, commands, and all project-specific guidance. This file exists only for Claude-specific behaviour.

## Claude-specific notes

- The memory system at `~/.claude/projects/*/memory/` holds session-persistent notes (MIDI address map, capture toolchain, etc.). Check it when working on MIDI or capture-related code.
- AGENTS.md is the source of truth; keep it updated when making architectural changes. Do not duplicate content here.

## Flet API reference (auto-loaded)

@.claude/commands/flet.md

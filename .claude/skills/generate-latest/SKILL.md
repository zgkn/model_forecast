---
name: generate-latest
description: Trigger the forecast workflow (pages.yml) on the default branch so the latest IFS, AIFS and CAMS runs are generated and published. Use when the user types /generate-latest or asks to generate the latest model run.
disable-model-invocation: true
allowed-tools: Bash(date:*), ToolSearch, mcp__github__actions_run_trigger
---

# Generate the latest model run

Just start the workflow; do not wait for it and do not report on the result.

Repository `zgkn/model_forecast`, workflow `pages.yml`, branch `claude/intelligent-bell-ep1v3e` (the default branch, so the
run also deploys GitHub Pages and refreshes the `data` branch).

## Steps

1. If the `mcp__github__*` tools are not loaded (they can disconnect and reconnect), load them with ToolSearch
   (query: `github actions run workflow trigger`). Without them, use `gh workflow run pages.yml --repo zgkn/model_forecast
   --ref claude/intelligent-bell-ep1v3e` if `gh` is installed; if neither works, say so and stop.
2. Trigger the workflow: `actions_run_trigger` with `run_workflow`, owner `zgkn`, repo `model_forecast`, workflow `pages.yml`,
   ref `claude/intelligent-bell-ep1v3e`.
3. Answer with one line: that the run was started and the time (`date -u`, UTC), or the error if the trigger failed.

Do not check the run, list the `data` branch, wait, or write a report. Do not change or push anything in the repository.

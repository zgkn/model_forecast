---
name: generate-latest
description: Trigger the forecast workflow (pages.yml) on the default branch, wait for it, and report which IFS, AIFS and CAMS runs are now live. Use when the user types /generate-latest or asks to generate the latest model run.
disable-model-invocation: true
allowed-tools: Bash(date:*), Bash(sleep:*), ToolSearch, mcp__github__actions_run_trigger, mcp__github__actions_list, mcp__github__actions_get, mcp__github__get_file_contents, mcp__github__get_job_logs
---

# Generate the latest model run

Repository `zgkn/model_forecast`, workflow `pages.yml`, branch `claude/intelligent-bell-ep1v3e` (the default branch, so it
also deploys GitHub Pages and refreshes the `data` branch). The workflow downloads the newest IFS / AIFS runs and the
newest CAMS cycle, builds the site and deploys it.

Do not change or push anything in the repository for this command, and do not create pull requests.

## Steps

1. **Tools.** If the `mcp__github__*` tools are not loaded (they can disconnect and reconnect), load them with
   ToolSearch (query: `github actions run workflow trigger`, then `select:mcp__github__actions_list,mcp__github__get_file_contents`
   if needed). Without them, fall back to `gh` if it is installed; if neither works, say so and stop.
2. **Before.** Record `date -u` and list the `data` branch (`get_file_contents`, path `/`, ref `refs/heads/data`, fields
   `name`, `size`). The files `ifs_<YYYYMMDDHH>.npz`, `aifs_<...>.npz`, `cams_<...>.npz` name the runs now live; the
   `cdd-ifs` / `cdd-aifs` files name the dry-day runs.
3. **Trigger.** `actions_run_trigger` with `run_workflow`, owner `zgkn`, repo `model_forecast`, workflow `pages.yml`,
   ref `claude/intelligent-bell-ep1v3e`. Tell the user the time it was triggered (UTC) and that you are waiting.
4. **Wait.** Never use a foreground sleep (it is blocked). Run `sleep 175; date -u` with `run_in_background: true`; you are
   notified when it ends. Then list the newest `pages.yml` run (`actions_list`, `list_workflow_runs`, `perPage` 1) and
   check it is the one you triggered (created just after the trigger time). A normal run takes 2 to 3 minutes. A run
   that is still `in_progress` usually waits for the Copernicus (CAMS) queue: wait another 60 to 120 seconds, up to
   about 12 minutes in total (the refresh step is time-boxed to 14 minutes).
5. **If it did not succeed** (`failure`, `cancelled`), find the failing step with `actions_list` / `list_workflow_jobs`
   and read the tail of the job log (`get_job_logs`, `tail_lines` about 150). Report the cause in a few lines. A
   run cancelled because a push or a scheduled run started in the same concurrency group is not an error: say so and
   check the newer run instead. Do not re-trigger automatically.
6. **After.** List the `data` branch again and compare with step 2.

## Report

Short, in this form (UTC times, no claim about what the live page looks like: you cannot open it):

- one line: run number and link, trigger time, result and duration;
- a table `Tab | Run now live | Before` for IFS, AIFS and CAMS (cycle written like `3 Nov 00Z`);
- only the notes that apply:
  - **Nothing new:** say which release is still due. Typical release times (UTC): IFS and AIFS about 7 h 50 min after the
    cycle (00Z about 07:50, 06Z 13:50, 12Z 19:50, 18Z 01:50 the next day; AIFS can be a little earlier); CAMS 00Z about 10:00
    and CAMS 12Z about 22:00. The scheduled runs are at 02:30, 08:30, 14:30 and 20:30 UTC.
  - **CAMS wind overlay:** the CAMS tab draws IFS wind at the same valid time. If the CAMS cycle starts before the IFS run,
    the frames before the IFS start have no wind (the header says "not available at this time"); give the number of
    hours (IFS cycle minus CAMS cycle). If the cycles are equal, say every CAMS frame has wind.
  - **Dry-day layer:** the IFS dry-day (CDD) layer only uses 00Z and 12Z runs, so when the IFS run is 06Z or 18Z the
    `cdd-ifs` file is one run behind; mention it only if it differs from the IFS run.
  - **Failures or warnings** found in step 5, and what the user can do.

Keep it brief; no recap of how the workflow works.

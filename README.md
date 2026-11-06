# job-autopilot

Finds job postings that fit you, screens each one against your own rubric,
and fills in the application forms with a headless browser. It answers only
from a profile you write. It never makes anything up, never solves CAPTCHAs,
and anything it can't answer truthfully goes back to you.

```
 fetch listings ──► screen live posting ──► queue + approve ──► fill form ──► submit ──► log
 (boards, trackers,  (role, location, years,  (you, or auto)      (profile table    (or block
  Jobright)           sponsorship, pay, score)                     + grounded model)  with a reason)
```

## What it does

- **Discovery.** Pulls listings from public Greenhouse / Ashby / Lever boards,
  GitHub new-grad tracker repos (markdown and HTML tables), Jobright public
  search pages, and optionally your personalised Jobright feed. Title and
  location regexes and a seen-store remove noise and repeats.
- **Screening.** Each new posting is opened and read by Claude against your
  `rubric.md`. It checks for a target role, an acceptable location, the
  experience cap, explicit "no visa sponsorship" (only when you need
  sponsorship) and pay above your floor, then scores fit from 0 to 100.
- **Applying.** Supports Greenhouse, Ashby and Lever forms, and Workday
  (sign-in or account creation, My Experience panels, multi-step flow).
  - Standard fields (contact details, work authorization, EEO, consents,
    salary, how-you-heard) are answered by deterministic rules from
    `profile.json`, with no model call.
  - The leftover fields, essays ("Why us?") and required cover letters are
    written by Claude, grounded only in your `cv.md` and `notes.md`.
- **Bookkeeping.** An approval-gated queue with a daily cap. A posting is
  deduplicated across URL variants. Results are recorded as
  submitted / unverified / blocked / failed, with a retry limit and
  screenshots of every step.

## Safety rails (by design)

- **Never fabricates.** Answers come from your profile files. A required
  question they can't answer *blocks* the job with the question listed for
  you, instead of guessing.
- **Never solves CAPTCHAs or "are you a bot" questions.** Those jobs are
  marked blocked for you to finish by hand.
- **Respects employer AI policies.** If a form states an AI-use policy for
  applications, no essay on it is model-written.
- **The model has no tools.** API calls carry no tools. The Claude Code CLI
  fallback runs with every tool, MCP server and connector disabled, from an
  empty directory, and this is checked at startup. So a prompt injection
  hidden in a job posting can at worst produce a bad answer, and every answer
  is validated against the field's options before it's typed.
- **Never double-submits.** A crash after the submit click is recorded as
  `unverified` and never retried automatically.
- **Your data stays local.** `profile/`, `data/`, `secrets/`, `runs/` and
  `.env` are git-ignored. Passwords and tokens are never sent to the model,
  logged or screenshotted.

You are responsible for what gets submitted in your name. Read your profile
carefully, start with `--dry-run`, and check the employer's and site's terms.

## Quick start

```bash
git clone https://github.com/<you>/job-autopilot && cd job-autopilot
bash deploy/setup.sh            # venv, Playwright Chromium, .env, profile/ from the template
```

1. **Fill in `profile/`** (copied from `profile.example/`):

   | File | What goes in it |
   |---|---|
   | `profile.json` | Contact details, work authorization, EEO answers, salary, start date, standing answers, employment and education entries (for Workday panels). Every value is typed into real forms. |
   | `cv.md` | Everything real about your experience. The model grounds essays and cover letters in this. |
   | `notes.md` | Optional extra facts and reusable answers. |
   | `rubric.md` | Your target roles, experience gate and how to score fit. |
   | `search.json` | Which company boards, trackers and Jobright pages to pull, plus the title and location filters. |
   | `resume.pdf` | Your résumé (or set `resume_pdf` in `profile.json`). |

2. **Set up a model.** Either put `ANTHROPIC_API_KEY=...` in `.env`, or
   install [Claude Code](https://claude.com/claude-code) and log in once
   (`claude`).

3. **Try it without submitting anything:**

   ```bash
   .venv/bin/python job_scout/fetch_jobs.py                      # what would be found
   .venv/bin/python -m job_scout.runner.discover --dry-run       # what would pass screening
   .venv/bin/python -m job_scout.runner.discover                 # screen + queue for real
   .venv/bin/python job_scout/auto_apply.py review               # numbered list of queued jobs
   .venv/bin/python job_scout/auto_apply.py approve 1 3 5-7      # approve some
   .venv/bin/python -m job_scout.runner.run --dry-run            # fill forms, never submit
   ```

   Look at the screenshots and `*-answers.json` files in `runs/<timestamp>/`.

4. **Go live:** `.venv/bin/python -m job_scout.runner.run`

## Running unattended

```bash
deploy/run_once.sh                                # one pass: discover, then apply
nohup setsid deploy/run_loop.sh >/dev/null 2>&1 & # a pass every RUNNER_LOOP_HOURS (default 2)
kill -- -"$(cat runs/loop.pid)"                   # stop the loop
```

The loop never types a password. It reuses the saved browser session.
Schedule `deploy/run_once.sh` from cron (see `deploy/crontab.txt`) for a
daily pass that can sign in to Workday. Passes share a lock, so they never
overlap. Log: `runs/run.log`.

By default, jobs that pass screening still wait for your approval
(`RUNNER_AUTO_APPROVE_DISCOVERED=0`). Set it to `1` to let discovery approve
them, up to `JOB_DAILY_CAP` submissions per day.

## Queue commands

```bash
python3 job_scout/auto_apply.py status            # pending, today's submits, blocked jobs + why
python3 job_scout/auto_apply.py skip 2 4          # never apply to these
python3 job_scout/auto_apply.py unblock URL       # retry a blocked/failed job after fixing the cause
python3 job_scout/auto_apply.py record URL --status submitted --note "applied by hand"
```

Jobs on sites without a form adapter are listed in `data/needs_you.md`.

## Optional integrations

- **Workday:** set `ATS_EMAIL` / `ATS_PASSWORD` in `.env`. The runner signs
  in, or creates the account on a new tenant. Per-tenant passwords go in
  `WORKDAY_PW_<TENANT>`.
- **Gmail (read-only):** lets the runner read emailed security codes
  (Greenhouse "confirm you're human" codes) and Workday verification links.
  Run `python3 deploy/gmail_auth.py CLIENT_ID CLIENT_SECRET` on a machine with
  a browser, using a Google Cloud OAuth desktop client with the Gmail API
  enabled. It writes `secrets/gmail_token.json` (scope `gmail.readonly`).
- **Jobright recommendations:** log in at jobright.ai, copy the `SESSION_ID`
  cookie, and save it as one line `SESSION_ID=<value>` in
  `secrets/jobright_session.txt` (`chmod 600`). The fetcher then reads your
  personalised feed and keeps only real employer links. When the session
  expires, the fetch report says so.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Use the Anthropic API (otherwise the `claude` CLI) |
| `RUNNER_CLAUDE_MODEL` | `claude-opus-5-5` | Model for screening and answers |
| `RUNNER_EFFORT` | `medium` | API effort level |
| `JOB_DAILY_CAP` | `10` | Max submissions per day |
| `RUNNER_MIN_PAY` | `120000` | Skip postings whose stated pay tops out at or below this |
| `RUNNER_ALLOW_UNKNOWN_PAY` | `1` | Apply when no pay is stated |
| `RUNNER_MIN_SCORE` | `40` | Minimum fit score to queue |
| `RUNNER_DISCOVER_MAX` | `60` | Postings screened per pass |
| `RUNNER_AUTO_APPROVE_DISCOVERED` | `0` | `1` = approve what passes screening automatically |
| `RUNNER_HEADLESS` | `1` | `0` shows the browser |
| `JOB_PROFILE_DIR` / `JOB_DATA_DIR` | `profile/` / `data/` | Move your profile or state elsewhere |

## Layout

```
job_scout/fetch_jobs.py      listing sources + filters
job_scout/auto_apply.py      approval queue / apply log CLI
job_scout/runner/discover.py screen new postings -> queue
job_scout/runner/run.py      apply to approved jobs (Greenhouse/Ashby/Lever)
job_scout/runner/workday.py  Workday flow
job_scout/runner/answers_table.py  deterministic profile answers
job_scout/runner/answerer.py screening + grounded model answers, essays, cover letters
job_scout/runner/form.py     generic form reading/filling (extract.js)
profile.example/             template profile (fictional person)
deploy/                      setup, run_once, run_loop, cron, Gmail auth
```

## Limitations

- The runner applies on Greenhouse, Ashby, Lever and Workday only. Custom
  careers sites go to `needs_you.md`.
- Forms change. Expect some `failed` / `blocked` results, and check
  `auto_apply.py status` now and then.
- The filters in `profile.example/search.json` are tuned for early-career
  ML/AI roles in the US. Edit the regexes and `rubric.md` for anything else.

## License

MIT

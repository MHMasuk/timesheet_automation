# RIMES Timesheet Automation

Fills in your RIMES MIS monthly timesheet (pmis.rimes.int) automatically:

1. **generate_details.py** — turns a text summary of what you worked on
   (e.g. a pasted GitHub commit history) into one distinct task line per
   working day, using a free Hugging Face AI model.
2. **fill_rimes_timesheet.py** — opens the timesheet in a real browser
   (via Playwright) and fills/saves each day's row with those lines.

It never clicks the final "Click to Submit" button — you review the
entries yourself and submit manually when you're happy with them.

All commands below assume you're inside this `timesheet/` folder:

```
cd timesheet
```

---

## 1. One-time setup

Install the dependencies (into the project's `venv`, or your own):

```
pip install -r requirements.txt
playwright install chromium
```

Get a free Hugging Face token at https://huggingface.co/settings/tokens
("Read" access is enough), and put it in `.env` in this folder:

```
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`.env` and `rimes_auth_state.json` are both listed in `.gitignore` —
never commit them, they're private to you.

## 2. Log in once and save your session

```
python fill_rimes_timesheet.py --login
```

A browser window opens — log in to pmis.rimes.int normally, then press
Enter in the terminal. This saves your session to `rimes_auth_state.json`
(cookies only, never your password). Keep that file private, and re-run
this step whenever your session expires or your password changes.

## 3. Write down what you worked on

Put a summary of the month's work into `context.txt` — free text, or a
pasted GitHub commit history page (the script auto-detects and cleans
that format, stripping author names/dates/merge noise).

## 4. Generate the daily task lines

```
python generate_details.py \
    --summary-file context.txt \
    --year 2026 --month 7 \
    --out details_generated.txt
```

This counts the working days in that month (skips Friday/Saturday by
default, same as the fill script) and asks the AI model for that many
distinct one-line task descriptions. Read through `details_generated.txt`
afterward — the model can make mistakes, so check it before using it.

## 5. Preview the timesheet fill (no changes made yet)

```
python fill_rimes_timesheet.py --year 2026 --month 7 \
    --details-file details_generated.txt --dry-run
```

This prints what it *would* fill in for each day, without clicking
anything on the real site.

## 6. Actually fill and save

Drop `--dry-run` once the preview looks right:

```
python fill_rimes_timesheet.py --year 2026 --month 7 \
    --details-file details_generated.txt
```

By default, days that already have a saved entry are left untouched. To
correct/re-save days that were already saved, add `--overwrite` (preview
it with `--dry-run` first, same as above):

```
python fill_rimes_timesheet.py --year 2026 --month 7 \
    --details-file details_generated.txt --overwrite --dry-run
```

## 7. Review and submit

Open pmis.rimes.int yourself, check the filled-in rows for the month, and
click "Click to Submit" manually. This script will never do that step for
you.

---

## Useful flags (fill_rimes_timesheet.py)

| Flag | Purpose |
|---|---|
| `--project` | Project name (default: "GCF Timor Leste Project") |
| `--component` | Component text (default: "2. Strengthened observations, monitoring, analysis and forecasting of climate and its impacts") |
| `--start` / `--end` | Work hours (default: 09:00 / 17:00) |
| `--skip-days` | Weekdays to skip (default: Friday Saturday) |
| `--headless` | Run without a visible browser window |
| `--overwrite` | Re-fill and re-save days that already have an entry |
| `--dry-run` | Preview only, no clicks |

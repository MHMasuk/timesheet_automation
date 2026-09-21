#!/usr/bin/env python3
"""
fill_rimes_timesheet.py

Fills in day rows on the RIMES MIS monthly timesheet (pmis.rimes.int) for a
given pay period, skipping whichever weekdays you tell it to (Friday and
Saturday by default) and skipping any day that already has a saved entry.

WHY PLAYWRIGHT INSTEAD OF RAW JS
---------------------------------
An earlier attempt drove the page with raw DOM manipulation (setting
.value and dispatching a generic 'change' event, then calling the site's
internal save_a_row() JS function directly). That looked like it worked
(it even showed a "Saved successfully!" toast once) but the data did not
actually persist on reload — the site's save handler expects a real click
event to find its row context, and a plain synthetic event isn't always
enough for a framework-bound form like this one. Playwright's
select_option()/.fill()/.click() dispatch fully realistic, trusted browser
events, which is what this site's own JS is listening for, so saves here
are verified by re-reading the row's ts_id after each click.

This script never touches the "Click to Submit" button on the My Timesheet
review page — that finalizes the whole period and should only ever be
clicked by you, after you've reviewed the entries yourself.

SETUP
-----
    pip install playwright
    playwright install chromium

STEP 1 - log in once and save your session (never stores your password):
    python fill_rimes_timesheet.py --login

    A browser window opens. Log in to pmis.rimes.int normally, then return
    to the terminal and press Enter. Your session cookies are saved to
    rimes_auth_state.json in this folder. Keep that file private — anyone
    with it can act as you on the site, the same as a copy of your login
    session. Delete it when you're done, or whenever you next change your
    password.

STEP 2 - fill in a period:
    python fill_rimes_timesheet.py --year 2026 --month 9 \\
        --project "GCF Timor Leste Project" \\
        --component "2. Strengthened observations, monitoring, analysis and forecasting of climate and its impacts" \\
        --start 09:00 --end 17:00 \\
        --details "Continued work on the demographic/climate-impact module." \\
        --dry-run

    Drop --dry-run once the preview looks right to actually click Save on
    each row. Use --details-file instead of --details to cycle through a
    different description per day (one line per entry, reused round-robin
    if there are more days than lines) — see details_example.txt.

    --skip-days defaults to Friday Saturday. Pass your own list, e.g.
    --skip-days Friday to only skip Fridays.

    By default, days that already have a saved entry are left untouched.
    Pass --overwrite to re-fill and re-save those days too (e.g. to correct
    a mistake) — note that for these rows the script can't confirm the save
    went through by watching ts_id (it's already populated from before), so
    review the result yourself afterward, ideally without --headless.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_URL = "https://pmis.rimes.int"
AUTH_STATE_FILE = "rimes_auth_state.json"


def do_login():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{BASE_URL}/")
        print("A browser window has opened. Log in to RIMES MIS normally.")
        input("Once you're logged in and can see your dashboard, press Enter here...")
        context.storage_state(path=AUTH_STATE_FILE)
        print(f"Session saved to {AUTH_STATE_FILE}. Keep this file private.")
        browser.close()


def load_details(details, details_file):
    if details_file:
        lines = [
            line.strip()
            for line in Path(details_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not lines:
            sys.exit(f"{details_file} has no non-empty lines.")
        return lines
    if not details:
        sys.exit("Provide either --details or --details-file.")
    return [details]


def fill_timesheet(year, month, project, component, start_time, end_time,
                    details_list, skip_days, dry_run, headless, overwrite):
    if not Path(AUTH_STATE_FILE).exists():
        sys.exit(f"No saved session found ({AUTH_STATE_FILE}). Run with --login first.")

    url = f"{BASE_URL}/Timesheets/insert_timesheet_monthly/{year}/{month:02d}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=AUTH_STATE_FILE)
        page = context.new_page()
        page.goto(url)
        page.wait_for_selector("table:has-text('Time In')")

        rows = page.locator(
            "table:has-text('Time In') tbody tr"
        ).filter(has=page.locator("input.date_list"))
        count = rows.count()
        print(f"Found {count} day rows for {year}-{month:02d}.")

        filled = skipped_day = skipped_existing = overwritten = failed = 0
        detail_i = 0

        for i in range(count):
            row = rows.nth(i)
            date_input = row.locator("input.date_list")
            date_str = date_input.input_value()  # dd/mm/yyyy
            d, m, y = (int(x) for x in date_str.split("/"))
            weekday = datetime(y, m, d).strftime("%A")

            if weekday in skip_days:
                skipped_day += 1
                continue

            ts_id_input = row.locator('input[name="ts_id"]')
            has_existing = bool(ts_id_input.input_value())
            if has_existing and not overwrite:
                print(f"Skipping {date_str} ({weekday}) — already has a saved entry.")
                skipped_existing += 1
                continue

            details_text = details_list[detail_i % len(details_list)]
            detail_i += 1

            action = "Overwriting" if has_existing else "Filling"
            print(f"{action} {date_str} ({weekday}): {details_text[:60]}")
            if dry_run:
                if has_existing:
                    overwritten += 1
                else:
                    filled += 1
                continue

            try:
                row.locator("select.project_list").select_option(label=project)

                comp_select = row.locator("select.comp_list")
                comp_handle = comp_select.element_handle()
                page.wait_for_function(
                    "el => el.options.length > 1", arg=comp_handle, timeout=5000
                )
                if component:
                    comp_select.select_option(label=component)

                row.locator('input[name="st_time[]"]').fill(start_time)
                row.locator('input[name="ed_time[]"]').fill(end_time)
                row.locator('textarea[name="details[]"]').fill(details_text)

                button_name = "Update" if has_existing else "Save"
                row.get_by_role("button", name=button_name).click()

                if has_existing:
                    # ts_id is already non-empty from the prior save, so it can't
                    # be used to confirm this click went through — just give the
                    # save request time to complete.
                    page.wait_for_timeout(1500)
                else:
                    ts_handle = ts_id_input.element_handle()
                    page.wait_for_function(
                        "el => el.value !== ''", arg=ts_handle, timeout=8000
                    )

                if has_existing:
                    overwritten += 1
                else:
                    filled += 1
            except (PWTimeout, Exception) as e:
                print(f"  FAILED on {date_str}: {e}")
                failed += 1

        print(
            f"\nDone. Filled {filled}, overwrote {overwritten}, "
            f"skipped {skipped_day} skip-day(s), "
            f"skipped {skipped_existing} already-saved day(s), {failed} failed."
        )
        if not dry_run and not headless:
            input("Review the entries in the browser, then press Enter to close...")
        browser.close()

        if failed:
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Fill in RIMES MIS monthly timesheet rows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--login", action="store_true", help="Log in once and save your session.")
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--project", default="GCF Timor Leste Project")
    parser.add_argument(
        "--component",
        default="2. Strengthened observations, monitoring, analysis and forecasting of climate and its impacts",
    )
    parser.add_argument("--start", default="09:00")
    parser.add_argument("--end", default="17:00")
    parser.add_argument("--details", default="")
    parser.add_argument("--details-file", default="")
    parser.add_argument(
        "--skip-days", nargs="*", default=["Friday", "Saturday"],
        help="Full weekday names to skip, e.g. Friday Saturday",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without clicking Save.")
    parser.add_argument(
        "--headless", action="store_true",
        help="Run without a visible browser window (default: visible, recommended so you can watch).",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-fill and re-save days that already have a saved entry, instead of skipping them. "
             "Use to correct mistakes in previously saved rows. Existing ts_id can't be used to "
             "confirm the save went through for these rows, so review the result yourself afterward.",
    )
    args = parser.parse_args()

    if args.login:
        do_login()
        return

    if not args.year or not args.month:
        parser.error("--year and --month are required unless using --login")

    details_list = load_details(args.details, args.details_file)

    fill_timesheet(
        year=args.year,
        month=args.month,
        project=args.project,
        component=args.component,
        start_time=args.start,
        end_time=args.end,
        details_list=details_list,
        skip_days=set(args.skip_days),
        dry_run=args.dry_run,
        headless=args.headless,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()

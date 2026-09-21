#!/usr/bin/env python3
"""
generate_details.py

Turns a free-text summary of what you worked on last month into a list of
distinct, one-line daily timesheet entries, using a Hugging Face-hosted
model. The output file is meant to be fed straight into
fill_rimes_timesheet.py's --details-file option, which already cycles
through lines round-robin, one per unfilled weekday.

SETUP
-----
    pip install huggingface_hub python-dotenv

    Get a free token at https://huggingface.co/settings/tokens
    (a "Read" token is enough) and put it in a .env file next to this
    script (already created for you, just fill in the value):

        HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxx

    Keep .env out of version control — never commit it.

    If the default model below isn't available on the free Inference API
    when you try it, open https://huggingface.co/models?pipeline_tag=text-generation&sort=trending,
    pick another instruct-tuned model that shows an "Inference" widget on
    its page, and pass it with --model.

USAGE
-----
Let it compute how many working days are in the period (same skip-days
logic as fill_rimes_timesheet.py, so the line count matches):

    python generate_details.py \\
        --summary "Worked on the demographic/climate-impact module, fixed \\
                    two database migration bugs, attended weekly project \\
                    sync meetings, drafted the Q3 progress report." \\
        --year 2026 --month 9 \\
        --out details_generated.txt

...or specify the line count directly with --days N instead of --year/--month.

Then preview/fill the timesheet with the generated lines:

    python fill_rimes_timesheet.py --year 2026 --month 9 \\
        --details-file details_generated.txt --dry-run
"""

import argparse
import calendar
import os
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import InferenceClient

load_dotenv()

DEFAULT_MODEL = "HuggingFaceH4/zephyr-7b-beta"
DEFAULT_PROVIDER = "featherless-ai"


def count_working_days(year, month, skip_days):
    days_in_month = calendar.monthrange(year, month)[1]
    return sum(
        1
        for d in range(1, days_in_month + 1)
        if date(year, month, d).strftime("%A") not in skip_days
    )


_GIT_LOG_NOISE_LINE = re.compile(
    r"^(authored|committed|Verified|today|yesterday|last week|last month|"
    r"\d+\s+(?:day|days|week|weeks|month|months)\s+ago|on\s+\w+\s+\d+|"
    r"Commits on\s+.+|Merge pull request\s+#\d+\s+from\s+\S+)$",
    re.IGNORECASE,
)


def clean_git_log_text(text):
    """Strip GitHub commit-history-page chrome (author names, 'committed',
    relative dates, truncated duplicate PR titles) down to just the
    substantive commit message lines, so the model isn't wading through
    mostly-noise input."""
    lines = [line.strip() for line in text.splitlines()]
    kept = [
        line for line in lines
        if line and not line.endswith("…") and not _GIT_LOG_NOISE_LINE.match(line)
    ]
    # Drop short lines that appear as an isolated consecutive duplicate pair
    # (an author name printed twice in a row) — real commit messages don't
    # repeat themselves like that.
    deduped = []
    i = 0
    while i < len(kept):
        if i + 1 < len(kept) and kept[i] == kept[i + 1] and len(kept[i]) < 60:
            i += 2
            continue
        deduped.append(kept[i])
        i += 1
    return "\n".join(deduped)


def looks_like_git_log(text):
    return bool(re.search(r"Merge pull request\s+#\d+\s+from", text))


_CONVERSATIONAL_MARKERS = (
    "thanks", "thank you", "can you", "could you", "please", "sure,", "sure!",
    "i can", "i'll", "let me know", "as an ai",
)

_GIT_NOISE_MARKERS = (
    "merged pull request", "merge pull request", "committed changes for pull request",
    "committed changes for pr",
)


def _looks_conversational(line):
    lower = line.lower()
    if line.endswith("?"):
        return True
    if len(line) > 220:
        return True
    if any(marker in lower for marker in _GIT_NOISE_MARKERS):
        return True
    return any(marker in lower for marker in _CONVERSATIONAL_MARKERS)


def generate_points(summary, days, model, token, provider):
    client = InferenceClient(model=model, token=token, provider=provider)
    prompt = (
        f"You are helping fill in a work timesheet. The summary below is "
        f"raw git activity (commit messages, PR merges, author/date noise "
        f"from a GitHub page). Based on the ACTUAL ENGINEERING WORK it "
        f"describes, write exactly {days} distinct, specific one-line task "
        f"descriptions suitable for daily timesheet entries. Vary the "
        f"wording and specific focus across lines so it doesn't read as "
        f"copy-pasted, but stay consistent with the summary — don't invent "
        f"work that isn't implied by it.\n\n"
        f"IMPORTANT: ignore and never output git/GitHub bookkeeping noise "
        f"such as 'Merged pull request #N', 'Committed changes for pull "
        f"request #N', author names, 'Verified', or relative dates like "
        f"'2 weeks ago' — those aren't real work. Describe only the actual "
        f"feature/fix/refactor being done, e.g. what the PR or commit "
        f"actually changed.\n\n"
        f"Output ONLY the {days} lines, one per line, no numbering, no "
        f"bullets, no extra commentary.\n\nSummary:\n{summary}"
    )
    last_err = None
    for attempt in range(3):
        try:
            response = client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
            )
            text = response.choices[0].message.content
            lines = []
            for line in text.splitlines():
                # Drop anything from a leaked chat-template control token
                # onward (e.g. "<|system|>", "<|user|>") — some models spill
                # these into the output when they run past their turn.
                line = line.split("<|", 1)[0]
                line = re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", line).strip()
                if not line or _looks_conversational(line):
                    continue
                lines.append(line)
            return lines
        except Exception as e:
            last_err = e
            print(f"  attempt {attempt + 1} failed ({str(e)[:120]}), retrying...")
    raise last_err


def main():
    parser = argparse.ArgumentParser(
        description="Generate daily timesheet detail lines from a summary via a Hugging Face model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--summary", default="", help="Free-text summary of the work.")
    parser.add_argument("--summary-file", default="", help="Path to a file containing the summary instead.")
    parser.add_argument("--days", type=int, default=0, help="Number of lines to generate directly.")
    parser.add_argument("--year", type=int, help="Used with --month to auto-count working days.")
    parser.add_argument("--month", type=int, help="Used with --year to auto-count working days.")
    parser.add_argument(
        "--skip-days", nargs="*", default=["Friday", "Saturday"],
        help="Weekdays not counted as working days (default: Friday Saturday).",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Hugging Face model id to use.")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, help="Hugging Face inference provider to route through.")
    parser.add_argument("--out", default="details_generated.txt")
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("Set HF_TOKEN in your environment first (see script docstring for how to get one).")

    if args.summary_file:
        summary = Path(args.summary_file).read_text(encoding="utf-8").strip()
    elif args.summary:
        summary = args.summary
    else:
        sys.exit("Provide --summary or --summary-file.")

    if looks_like_git_log(summary):
        before = len(summary.splitlines())
        summary = clean_git_log_text(summary)
        after = len(summary.splitlines())
        print(f"Detected a raw GitHub commit log — cleaned {before} lines down to {after}.")

    if args.days:
        days = args.days
    elif args.year and args.month:
        days = count_working_days(args.year, args.month, set(args.skip_days))
    else:
        sys.exit("Provide --days, or both --year and --month to auto-count working days.")

    print(f"Requesting {days} task lines from {args.model} (via {args.provider})...")
    lines = generate_points(summary, days, args.model, token, args.provider)

    if len(lines) < days:
        print(f"Warning: model returned {len(lines)} lines, requested {days}. "
              f"fill_rimes_timesheet.py will cycle through them round-robin to cover the rest.")
    elif len(lines) > days:
        lines = lines[:days]

    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} lines to {args.out}:\n")
    for line in lines:
        print(f"  - {line}")


if __name__ == "__main__":
    main()

"""Render the four statistics panels shown on the profile README.

    python scripts/generate_stats.py

Writes generated/stats.svg, streak.svg, languages.svg and contributions.svg.
Requires only the Python standard library and a GitHub token; see gh_api.py
for how the token and login are resolved.
"""

from __future__ import annotations

import sys

import design as d
import gh_api

W = d.WIDTH
PAD = d.PAD
INNER = W - 2 * PAD


def _num(value: int) -> str:
    """Thin-space thousands separators read better than commas in monospace."""
    return f"{value:,}".replace(",", "\u2009")


# --------------------------------------------------------------------------- #
# stats.svg - headline total, breakdown, and week-by-week activity
# --------------------------------------------------------------------------- #

def stats_panel(c: gh_api.Contributions) -> str:
    height = 268
    body = [d.card(W, height), d.eyebrow("CONTRIBUTION SUMMARY", f"{c.window.label()} \u00b7 UTC")]

    total = _num(c.total)
    body.append(d.text(PAD, 96, total, size=44, weight=700, color="ink"))
    body.append(d.text(PAD, 118, "TOTAL CONTRIBUTIONS", size=9.5, color="ink3", tracking=1.5))

    # Breakdown columns, right-aligned as a block so the four values read as a
    # single unit against the headline number on the left.
    metrics = [
        ("COMMITS", c.commits),
        ("PULL REQUESTS", c.pull_requests),
        ("ISSUES", c.issues),
        ("REVIEWS", c.reviews),
    ]
    column = INNER / 2 / len(metrics)
    origin = PAD + INNER / 2
    for i, (label, value) in enumerate(metrics):
        x = origin + i * column
        body.append(d.text(x, 72, label, size=8.5, color="ink3", tracking=1.2))
        body.append(d.text(x, 98, _num(value), size=19, weight=700, color="ink"))

    body.append(d.rule(PAD, 146, W - PAD))

    weekly = c.weekly_totals()
    peak = max(weekly) if weekly else 0
    body.append(d.eyebrow("WEEKLY ACTIVITY", f"PEAK {peak} IN ONE WEEK", y=170))

    baseline = 236
    span = 50
    pitch, bar = d.group(len(weekly), INNER, ratio=0.6)
    body.append(d.rule(PAD, baseline + 0.5, W - PAD))
    for i, value in enumerate(weekly):
        x = PAD + i * pitch + (pitch - bar) / 2
        if peak and value:
            tall = max(2.0, value / peak * span)
            # Reserve the accent for the standout weeks so the chart still
            # reads as monochrome at a glance.
            colour = "accent" if value >= peak * 0.75 else "ink3"
            body.append(d.rect(x, baseline - tall, bar, tall, color=colour, radius=1))
        else:
            body.append(d.rect(x, baseline - 2, bar, 2, color="c0", radius=1))

    return d.document(
        W, height, "".join(body),
        title=f"{c.total} GitHub contributions in the last 365 days, with commit, "
              f"pull request, issue and review counts and week-by-week activity",
    )


# --------------------------------------------------------------------------- #
# streak.svg - current streak, longest streak, consistency
# --------------------------------------------------------------------------- #

def streak_panel(c: gh_api.Contributions) -> str:
    height = 172
    current = c.current_streak()
    longest = c.longest_streak()
    active = c.active_days
    rate = round(active / len(c.days) * 100) if c.days else 0

    columns = [
        ("CURRENT STREAK", f"{current.length}", "DAYS", current.label()),
        ("LONGEST STREAK", f"{longest.length}", "DAYS", longest.label()),
        ("ACTIVE DAYS", f"{active}", f"OF {len(c.days)}", f"{rate}% OF THE YEAR"),
    ]

    body = [d.card(W, height), d.eyebrow("CONSISTENCY", "COMPLETE UTC DAYS")]
    column = INNER / 3
    for i, (label, value, unit, note) in enumerate(columns):
        x = PAD + i * column
        if i:
            body.append(d.vrule(x - 24, 56, 140))
        body.append(d.text(x, 74, label, size=8.5, color="ink3", tracking=1.2))
        # The accent marks the live streak; historical figures stay in ink.
        body.append(d.text(x, 114, value, size=34, weight=700, color="accent" if i == 0 else "ink"))
        offset = len(value) * 34 * d.ADVANCE + 7
        body.append(d.text(x + offset, 114, unit, size=9, color="ink3", tracking=1))
        body.append(d.text(x, 136, note, size=9, color="ink2"))

    return d.document(
        W, height, "".join(body),
        title=f"Current contribution streak {current.length} days, longest streak "
              f"{longest.length} days, active on {active} of {len(c.days)} days",
    )


# --------------------------------------------------------------------------- #
# languages.svg - distribution across public repositories
# --------------------------------------------------------------------------- #

def languages_panel(languages: gh_api.Languages) -> str:
    entries = languages.entries
    total = languages.total
    if not entries or not total:
        raise gh_api.GitHubError("no language data returned for any public repository")

    rows = (len(entries) + 1) // 2
    height = 96 + rows * 30 + 16
    body = [
        d.card(W, height),
        d.eyebrow("LANGUAGE DISTRIBUTION", f"{languages.repo_count} PUBLIC REPOSITORIES \u00b7 BY BYTES"),
    ]

    # Single-hue ramp: the leading language is the accent, each subsequent one
    # steps toward neutral grey.
    last_shade = len(d.LIGHT["ramp"]) - 1
    bar_y, bar_h = 52, 10
    x = float(PAD)
    for i, (_, size) in enumerate(entries):
        width = INNER * size / total
        if i == len(entries) - 1:
            width = W - PAD - x  # absorb rounding into the final segment
        body.append(d.rect(x, bar_y, max(width - 1.5, 0.8), bar_h,
                           color=f"r{min(i, last_shade)}", radius=2))
        x += width

    for i, (name, size) in enumerate(entries):
        col, row = i % 2, i // 2
        left = PAD + col * (INNER / 2)
        right = left + INNER / 2 - (24 if col == 0 else 0)
        y = 100 + row * 30
        body.append(d.rect(left, y - 8, 8, 8, color=f"r{min(i, last_shade)}", radius=1.5))
        body.append(d.text(left + 16, y, name, size=11, color="ink"))
        body.append(d.text(right, y, f"{size / total * 100:.1f}%", size=10.5, color="ink2", anchor="end"))

    summary = ", ".join(f"{name} {size / total * 100:.1f}%" for name, size in entries)
    return d.document(
        W, height, "".join(body),
        title=f"Language distribution across {languages.repo_count} public repositories: {summary}",
    )


# --------------------------------------------------------------------------- #
# contributions.svg - one discrete cell per UTC day
# --------------------------------------------------------------------------- #

CELL = 11
GAP = 3
PITCH = CELL + GAP


def _thresholds(counts: list[int]) -> list[int]:
    """Quartile cut-offs over active days only.

    Scaling to the actual distribution keeps a quiet year legible instead of
    flattening every cell to the lowest level, and quartiles of a fixed data
    set are deterministic.
    """
    active = sorted(n for n in counts if n > 0)
    if not active:
        return [1, 2, 3, 4]
    def quantile(q: float) -> int:
        return active[min(len(active) - 1, int(q * (len(active) - 1)))]
    cuts = [1, max(2, quantile(0.5)), max(3, quantile(0.78)), max(4, quantile(0.94))]
    for i in range(1, len(cuts)):  # strictly increasing
        cuts[i] = max(cuts[i], cuts[i - 1] + 1)
    return cuts


def _level(count: int, cuts: list[int]) -> int:
    if count <= 0:
        return 0
    for i, cut in enumerate(cuts[1:], start=1):
        if count < cut:
            return i
    return 4


def contributions_panel(c: gh_api.Contributions) -> str:
    """Contribution activity summary — metrics and legend only, no day grid.

    The snake is the sole calendar visualization on the profile. This panel
    keeps the activity headline, busiest day, and intensity legend so those
    numbers stay visible without duplicating the grid.
    """
    height = 88
    body = [
        d.card(W, height),
        d.eyebrow("CONTRIBUTION ACTIVITY", f"{len(c.days)} DAYS \u00b7 {_num(c.total)} CONTRIBUTIONS"),
    ]

    legend_y = 68
    legend_w = 5 * PITCH - GAP
    legend_x = W - PAD - legend_w - 42
    body.append(d.text(legend_x - 8, legend_y, "LESS", size=8, color="ink3", anchor="end", tracking=0.8))
    for i in range(5):
        body.append(d.rect(legend_x + i * PITCH, legend_y - CELL + 2.5, CELL, CELL, color=f"c{i}", radius=2))
    body.append(d.text(legend_x + legend_w + 8, legend_y, "MORE", size=8, color="ink3", tracking=0.8))

    busiest = c.busiest
    if busiest and busiest.count:
        body.append(d.text(PAD, legend_y, f"BUSIEST DAY  {busiest.date.strftime('%d %b %Y').upper()}"
                                          f"  \u00b7  {busiest.count} CONTRIBUTIONS",
                           size=9, color="ink2", tracking=0.4))

    return d.document(
        W, height, "".join(body),
        title=f"Contribution activity for the last {len(c.days)} days: "
              f"{c.total} contributions, busiest day {busiest.date if busiest else 'n/a'}",
    )


# --------------------------------------------------------------------------- #

def _report(c: gh_api.Contributions) -> None:
    """Print the numbers the contribution panels are built from.

    The invariant that matters is that the API total and the sum of the daily
    values agree; everything else is here to make a disagreement diagnosable.
    """
    busiest = c.busiest
    print()
    print(f"API total contributions: {c.total}")
    print(f"Daily contribution sum:  {c.daily_sum}")
    print(f"Number of weeks:         {len(c.weeks)}")
    print(f"Number of days:          {len(c.days)}"
          f"{'' if len(c.days) == len(c.calendar_days) else f' (of {len(c.calendar_days)} returned)'}")
    print(f"Reconciled via:          {c.reconciliation}")
    print(f"Calendar range:          {c.days[0].date} .. {c.days[-1].date}" if c.days else "")
    print(f"Active days:             {c.active_days}")
    if busiest:
        print(f"Busiest day:             {busiest.date}")
        print(f"Busiest day contributions: {busiest.count}")
    print(f"Private contributions:   {c.restricted}"
          f"{'' if c.has_restricted else '  (none reported by this token)'}")
    print(f"Breakdown:               commits={c.commits} prs={c.pull_requests} "
          f"issues={c.issues} reviews={c.reviews}")
    print()


def main() -> int:
    try:
        login = gh_api.resolve_login()
        token_source, token = gh_api.resolve_token()
        window = gh_api.current_window()
        print(f"login   {login}")
        print(f"window  {window.from_iso} .. {window.to_iso}")
        # Name only. The token value is never printed or written anywhere.
        print(f"token   {token_source}")

        contributions = gh_api.fetch_contributions(login, window, token=token)
        _report(contributions)
        # Hard stop if the headline total and the daily grid disagree.
        contributions.verify()

        languages = gh_api.fetch_languages(login, token=token)
    except gh_api.GitHubError as exc:
        print(f"\nrefresh aborted: {exc}", file=sys.stderr)
        return 1

    panels = {
        "stats.svg": stats_panel(contributions),
        "streak.svg": streak_panel(contributions),
        "languages.svg": languages_panel(languages),
        "contributions.svg": contributions_panel(contributions),
    }
    for name, content in panels.items():
        path = d.write(name, content)
        print(f"wrote   {path.relative_to(d.ROOT).as_posix():<28} {path.stat().st_size:>6} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

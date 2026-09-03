"""Standard-library GitHub client, plus the contribution maths built on it.

No third-party packages and no third-party services: everything the statistics
panels display is fetched straight from api.github.com and computed here.

Determinism is deliberate. The reporting window is always a whole number of
complete UTC calendar days, so re-running the workflow at 05:17 or at 05:49
produces byte-identical SVGs. Only the calendar date rolling over changes the
output.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = "https://api.github.com"
WINDOW_DAYS = 365  # today plus the 364 days before it

ROOT = Path(__file__).resolve().parent.parent


class GitHubError(RuntimeError):
    """Raised when GitHub cannot be reached or refuses the request.

    Callers must let this propagate: a failed refresh has to fail loudly rather
    than silently publish stale or invented numbers.
    """


# --------------------------------------------------------------------------- #
# Identity and credentials
# --------------------------------------------------------------------------- #

def resolve_login() -> str:
    """Work out whose profile this is, preferring repository metadata."""
    for var in ("PROFILE_LOGIN", "GITHUB_REPOSITORY_OWNER"):
        if os.environ.get(var):
            return os.environ[var].strip()
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"].split("/", 1)[0]
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True, cwd=ROOT,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitHubError("cannot determine the GitHub login; set PROFILE_LOGIN") from exc
    match = re.search(r"github\.com[:/]([^/]+)/", url)
    if not match:
        raise GitHubError(f"cannot parse a GitHub login out of remote {url!r}")
    return match.group(1)


def resolve_token() -> tuple[str, str]:
    """Return ``(variable name, token)``.

    PROFILE_TOKEN is checked first: it is the one that can carry ``read:user``,
    which is what makes private contribution counts appear in the calendar. The
    name is returned so callers can log which credential was used without ever
    logging the credential itself.
    """
    for var in ("PROFILE_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return var, value
    raise GitHubError(
        "no token found. GitHub's GraphQL API requires authentication even for "
        "public data. Set GITHUB_TOKEN (Actions provides it automatically) or "
        "export a token locally: $env:GITHUB_TOKEN = '...'"
    )


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #

def _request(url: str, *, token: str, payload: dict | None = None, accept: str) -> dict | list:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method="POST" if body else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "Content-Type": "application/json",
            "User-Agent": "self-generating-profile",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise GitHubError(f"HTTP {exc.code} from {url}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"network failure talking to {url}: {exc.reason}") from exc


def graphql(query: str, variables: dict, *, token: str) -> dict:
    data = _request(
        f"{API}/graphql",
        token=token,
        payload={"query": query, "variables": variables},
        accept="application/json",
    )
    if isinstance(data, dict) and data.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in data["errors"])
        raise GitHubError(
            f"GraphQL error: {messages}\n"
            "If this mentions permissions or resource access, add a fine-grained "
            "token with read-only access to public data as the PROFILE_TOKEN secret."
        )
    return data["data"]


def rest(path: str, *, token: str) -> dict | list:
    return _request(f"{API}{path}", token=token, accept="application/vnd.github+json")


# --------------------------------------------------------------------------- #
# Reporting window
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Window:
    start: dt.date
    end: dt.date

    @property
    def from_iso(self) -> str:
        return f"{self.start.isoformat()}T00:00:00Z"

    @property
    def to_iso(self) -> str:
        return f"{self.end.isoformat()}T23:59:59Z"

    def label(self) -> str:
        return f"{self.start.strftime('%d %b %Y').upper()} \u2192 {self.end.strftime('%d %b %Y').upper()}"


def current_window(today: dt.date | None = None) -> Window:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    return Window(start=today - dt.timedelta(days=WINDOW_DAYS - 1), end=today)


# --------------------------------------------------------------------------- #
# Contribution calendar
# --------------------------------------------------------------------------- #

CALENDAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    name
    login
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      # Private/internal activity, only populated when the token carries
      # read:user. Reported as an aggregate count and nothing else - no
      # repository names, URLs or languages are requested anywhere.
      restrictedContributionsCount
      hasAnyRestrictedContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date weekday contributionCount } }
      }
    }
  }
}
"""


@dataclass
class Day:
    date: dt.date
    count: int
    weekday: int  # 0 = Sunday, matching GitHub's calendar layout


@dataclass
class Streak:
    length: int = 0
    start: dt.date | None = None
    end: dt.date | None = None

    def label(self) -> str:
        if not self.length or self.start is None or self.end is None:
            return "NO ACTIVE STREAK"
        fmt = "%d %b"
        if self.start == self.end:
            return self.start.strftime(fmt).upper()
        return f"{self.start.strftime(fmt).upper()} \u2192 {self.end.strftime(fmt).upper()}"


@dataclass
class Contributions:
    window: Window
    weeks: list[list[Day]]
    total: int
    commits: int
    pull_requests: int
    issues: int
    reviews: int
    restricted: int = 0
    has_restricted: bool = False
    display_name: str = ""
    reconciliation: str = ""
    _days: list[Day] = field(default_factory=list)

    @property
    def calendar_days(self) -> list[Day]:
        """Every day the API returned, including any week padding."""
        return [d for week in self.weeks for d in week]

    @property
    def days(self) -> list[Day]:
        """The days all counting is done over, reconciled against the API total.

        GitHub aligns the calendar to whole Sunday-to-Saturday weeks, so it can
        return more days than the window asked for - its own web calendar
        currently spans 369. Whether the padding days carry counts decides which
        set of days actually adds up to ``totalContributions``, so rather than
        assuming one behaviour we pick whichever set reconciles and record the
        choice in ``reconciliation``.
        """
        if self._days:
            return self._days

        everything = self.calendar_days
        windowed = [d for d in everything if self.window.start <= d.date <= self.window.end]

        if sum(d.count for d in everything) == self.total:
            self._days, self.reconciliation = everything, "full calendar"
        elif sum(d.count for d in windowed) == self.total:
            self._days, self.reconciliation = windowed, "clipped to window (API padded the calendar)"
        else:
            # Neither reconciles. Keep everything so the diagnostics can show
            # both sums, and let verify() stop the run.
            self._days, self.reconciliation = everything, "MISMATCH"
        return self._days

    @property
    def daily_sum(self) -> int:
        """Total rebuilt from the daily values, for cross-checking against the API."""
        return sum(d.count for d in self.days)

    def verify(self) -> None:
        """Fail loudly when no set of daily values reproduces the API total.

        Publishing a chart whose cells contradict the number printed above it is
        worse than publishing nothing, so this is a hard error.
        """
        self.days  # force reconciliation
        if self.reconciliation == "MISMATCH":
            everything = self.calendar_days
            windowed = [d for d in everything if self.window.start <= d.date <= self.window.end]
            raise GitHubError(
                f"contribution calendar does not reconcile. The API reports "
                f"totalContributions={self.total}, but the daily contributionCount "
                f"values sum to {sum(d.count for d in everything)} across all "
                f"{len(everything)} returned days and {sum(d.count for d in windowed)} "
                f"across the {len(windowed)} days inside the requested window. "
                f"Refusing to generate statistics from contradictory data."
            )

    @property
    def active_days(self) -> int:
        return sum(1 for d in self.days if d.count)

    @property
    def busiest(self) -> Day | None:
        return max(self.days, key=lambda d: (d.count, d.date)) if self.days else None

    def weekly_totals(self) -> list[int]:
        return [sum(d.count for d in week) for week in self.weeks]

    def current_streak(self) -> Streak:
        """Consecutive active days ending today.

        Today is skipped when it is still empty: a workflow that runs at 05:17
        UTC would otherwise report a broken streak every morning.
        """
        days = self.days
        if not days:
            return Streak()
        index = len(days) - 1
        if days[index].count == 0:
            index -= 1
        end = index
        while index >= 0 and days[index].count > 0:
            index -= 1
        length = end - index
        if length <= 0:
            return Streak()
        return Streak(length=length, start=days[index + 1].date, end=days[end].date)

    def longest_streak(self) -> Streak:
        best = Streak()
        run = 0
        for i, day in enumerate(self.days):
            run = run + 1 if day.count else 0
            if run > best.length:
                best = Streak(length=run, start=self.days[i - run + 1].date, end=day.date)
        return best


def fetch_contributions(login: str, window: Window, *, token: str) -> Contributions:
    payload = graphql(
        CALENDAR_QUERY,
        {"login": login, "from": window.from_iso, "to": window.to_iso},
        token=token,
    )
    user = payload.get("user")
    if not user:
        raise GitHubError(f"GitHub returned no user named {login!r}")
    collection = user["contributionsCollection"]
    calendar = collection["contributionCalendar"]

    weeks = [
        [
            Day(dt.date.fromisoformat(d["date"]), int(d["contributionCount"]), int(d["weekday"]))
            for d in week["contributionDays"]
        ]
        for week in calendar["weeks"]
    ]
    if not any(weeks):
        raise GitHubError(
            "the contribution calendar came back empty. This usually means the "
            "token cannot read user contribution data; add a fine-grained token "
            "with read-only public access as the PROFILE_TOKEN secret."
        )

    return Contributions(
        window=window,
        weeks=weeks,
        # contributionCalendar.totalContributions is authoritative: it is the
        # same number GitHub prints on the profile page.
        total=int(calendar["totalContributions"]),
        commits=int(collection["totalCommitContributions"]),
        pull_requests=int(collection["totalPullRequestContributions"]),
        issues=int(collection["totalIssueContributions"]),
        reviews=int(collection["totalPullRequestReviewContributions"]),
        restricted=int(collection.get("restrictedContributionsCount") or 0),
        has_restricted=bool(collection.get("hasAnyRestrictedContributions")),
        display_name=user.get("name") or user["login"],
    )


# --------------------------------------------------------------------------- #
# Languages
# --------------------------------------------------------------------------- #

@dataclass
class Languages:
    entries: list[tuple[str, int]]  # (name, bytes), descending
    repo_count: int

    @property
    def total(self) -> int:
        return sum(b for _, b in self.entries)


def fetch_languages(login: str, *, token: str, limit: int = 9, min_share: float = 0.004) -> Languages:
    """Aggregate language bytes across public, non-forked repositories only.

    Forks are excluded because their language totals describe someone else's
    code, and private repositories are never requested at all, so no private
    metadata can leak into the committed SVG - not even when the token is
    scoped widely enough to read it.

    ``limit`` is one less than the colour ramp so every entry, including the
    trailing "Other", gets its own swatch. ``min_share`` folds away anything
    too small to draw as a visible bar segment.
    """
    repos: list[dict] = []
    page = 1
    while True:
        batch = rest(f"/users/{login}/repos?type=owner&per_page=100&page={page}", token=token)
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    public = [r for r in repos if not r.get("private") and not r.get("fork")]

    totals: dict[str, int] = {}
    for repo in public:
        breakdown = rest(f"/repos/{login}/{repo['name']}/languages", token=token)
        if not isinstance(breakdown, dict):
            continue
        for language, size in breakdown.items():
            totals[language] = totals.get(language, 0) + int(size)

    # Sort by size, then name, so ties never reorder between runs.
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    grand_total = sum(size for _, size in ranked) or 1

    head, tail = [], []
    for i, (name, size) in enumerate(ranked):
        (head if i < limit and size / grand_total >= min_share else tail).append((name, size))
    if tail:
        head.append(("Other", sum(size for _, size in tail)))
    return Languages(entries=head, repo_count=len(public))

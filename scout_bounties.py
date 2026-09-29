import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# Configuration
STATE_FILE = "seen_bounties.json"
MAX_COMMENTS = 12  # Personal profile: prefer lower-competition work
MAX_PRIORITY_COMMENTS = 3
COMMENT_REVIEW_LIMIT = 10

MOBILE_AI_TERMS = [
    "documentation", "docs", "readme", "markdown", "typo", "broken link",
    "accessibility", "wcag", "audit", "review", "quality", "test", "bug",
    "reproduce", "python", "javascript", "typescript", "github"
]
HARD_ENV_TERMS = [
    "android studio", "xcode", "ios device", "gpu required", "cuda",
    "windows only", "macos only", "hardware required", "onsite"
]
PAYMENT_TERMS = [
    "$", "usd", "usdc", "reward", "bounty", "paid", "payment",
    "opire", "algora", "drips", "rewarded"
]
APPLY_FIRST_LABELS = {
    "stellar wave",
    "drips wave",
    "wave bounty",
}
META_ALERT_LABELS = {"bounty-alert"}

ECONOMIC_PRIORITY = {
    "FUNDED": 5,
    "VERIFY": 4,
    "APPLY_FIRST": 3,
    "UNKNOWN": 2,
    "UNFUNDED_PROPOSAL": 1,
    "ALREADY_IMPLEMENTED": 0,
}
SUPPRESSED_ECONOMIC_STATUSES = {"UNFUNDED_PROPOSAL", "ALREADY_IMPLEMENTED"}
COMMENT_REVIEW_STATUSES = {"FUNDED", "VERIFY", "APPLY_FIRST"}

# GitHub search queries for active bounty opportunities
SEARCH_QUERIES = [
    'is:issue is:open bounty in:title,body sort:updated-desc',
    'is:issue is:open reward bounty sort:updated-desc',
    'is:issue is:open "paid" "PR" "bounty" sort:updated-desc',
    'is:issue is:open "Opire" bounty sort:updated-desc',
    'is:issue is:open "Algora" bounty sort:updated-desc',
    'is:issue is:open "USDC" bounty sort:updated-desc',
    'is:issue is:open "reward" documentation sort:updated-desc',
    'is:issue is:open "bounty" accessibility sort:updated-desc',
]


def load_seen_bounties():
    """Load previously seen bounty URLs from the state file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception as e:
            print(f"Error loading state file: {e}")
    return set()


def save_seen_bounties(seen_urls):
    """Save seen URLs in stable order so state commits stay small."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(set(seen_urls)), f, indent=2)
    except Exception as e:
        print(f"Error saving state file: {e}")


def github_headers(token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MyPersonalBountyScout",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def search_github(query, token=None):
    """Fetch search results from GitHub Issues API."""
    url = f"https://api.github.com/search/issues?{urllib.parse.urlencode({'q': query, 'per_page': 15})}"
    req = urllib.request.Request(url, headers=github_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"GitHub Search API Error for query '{query}': {e}")
        return {}


def fetch_issue_comments(comments_url, token=None):
    """Fetch issue comments for final economic/actionability review."""
    if not comments_url:
        return []
    separator = "&" if "?" in comments_url else "?"
    url = f"{comments_url}{separator}per_page=100"
    req = urllib.request.Request(url, headers=github_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"GitHub Comments API Error for '{comments_url}': {e}")
        return []


def extract_label_names(item):
    """Normalize GitHub label objects/strings to lowercase names."""
    names = []
    for label in item.get("labels") or []:
        if isinstance(label, dict):
            name = label.get("name")
        else:
            name = label
        if name:
            names.append(str(name).strip().lower())
    return sorted(set(names))


def repo_from_issue_url(url):
    """Extract owner/repo from a canonical GitHub issue URL."""
    prefix = "https://github.com/"
    if not str(url).startswith(prefix) or "/issues/" not in str(url):
        return ""
    return str(url).split("/issues/", 1)[0].replace(prefix, "", 1)


def comments_url_from_issue(item):
    """Return the API comments URL from search data or derive it from html_url."""
    if item.get("comments_url"):
        return item["comments_url"]
    issue_url = str(item.get("html_url", ""))
    repo = repo_from_issue_url(issue_url)
    if not repo or "/issues/" not in issue_url:
        return ""
    number = issue_url.rsplit("/issues/", 1)[-1].split("/", 1)[0]
    if not number.isdigit():
        return ""
    return f"https://api.github.com/repos/{repo}/issues/{number}/comments"


def is_meta_alert(item):
    """Recognize scanner-generated alert issues so alerts never become candidates."""
    label_names = set(extract_label_names(item))
    title = str(item.get("title", "")).strip().lower()
    return bool(label_names.intersection(META_ALERT_LABELS)) or title.startswith(
        "🎯 bounty alert:"
    )


def is_clean_candidate(item):
    """Triage logic to filter out noisy, assigned, closed, or spam tasks."""
    if "pull_request" in item:
        return False
    if item.get("assignees"):
        return False
    if int(item.get("comments", 0)) > MAX_COMMENTS:
        return False

    title = str(item.get("title", "")).lower()
    body = str(item.get("body", "")).lower()

    blocklist = [
        "airdrop", "referral", "casino", "gambling", "trading bot",
        "blog post", "article writing", "tutorial proposal", "content creator"
    ]
    if any(term in title or term in body for term in blocklist):
        return False

    return True


def classify_economic_status(text, payment_hits, label_names=None):
    """Classify whether a candidate is actually actionable economic work."""
    label_names = set(label_names or [])

    already_implemented_terms = [
        "implementation pr:",
        "implementation pull request:",
        "submitted pr:",
        "submitted pull request:",
    ]
    has_pull_url = "github.com/" in text and "/pull/" in text
    has_pull_label = any(
        label in text
        for label in ("pull request", "implementation pr", "submitted pr")
    )
    if any(term in text for term in already_implemented_terms) or (
        has_pull_url and has_pull_label
    ):
        return "ALREADY_IMPLEMENTED"

    unfunded_terms = [
        "unfunded proposal",
        "not an approved award",
        "not an existing award",
        "not an existing bounty",
        "proposed amount, not",
        "proposed amount — not",
        "proposed amount - not",
    ]
    if any(term in text for term in unfunded_terms):
        return "UNFUNDED_PROPOSAL"

    apply_first_terms = [
        "wait for assignment",
        "before coding",
        "before starting implementation",
        "apply to work on this issue",
        "contributor application",
        "must be assigned",
    ]
    if label_names.intersection(APPLY_FIRST_LABELS) or any(
        term in text for term in apply_first_terms
    ):
        return "APPLY_FIRST"

    funded_terms = [
        "funded bounty",
        "bounty funded",
        "reward is reserved",
        "reward reserved",
        "funds are reserved",
        "funds reserved",
        "funded by algora",
        "funded on algora",
    ]
    if any(term in text for term in funded_terms):
        return "FUNDED"

    if payment_hits:
        return "VERIFY"
    return "UNKNOWN"


def classify_comment_status(comments):
    """Use strong comment signals to detect claimed work or application gates."""
    saw_apply_first = False
    for comment in comments or []:
        body = str(comment.get("body", "")).lower()
        has_pull_url = "github.com/" in body and "/pull/" in body
        completion_terms = [
            "/claim",
            "i have submitted",
            "submitted a clean",
            "submitted pr",
            "submitted pull request",
            "resolving this issue",
            "implementation is complete",
        ]
        if has_pull_url and any(term in body for term in completion_terms):
            return "ALREADY_IMPLEMENTED"

        apply_terms = [
            "has applied to work on this issue",
            "review their application",
            "once assigned",
            "please assign me",
            "wait for assignment",
        ]
        if any(term in body for term in apply_terms):
            saw_apply_first = True

    if saw_apply_first:
        return "APPLY_FIRST"
    return None


def classify_candidate(item):
    """Rank technical fit and economic actionability independently."""
    title = str(item.get("title", ""))
    body = str(item.get("body", ""))
    text = (title + "\n" + body).lower()
    comments = int(item.get("comments", 0))
    label_names = extract_label_names(item)

    mobile_hits = sorted({term for term in MOBILE_AI_TERMS if term in text})
    hard_hits = sorted({term for term in HARD_ENV_TERMS if term in text})
    payment_text = text + "\n" + " ".join(label_names)
    payment_hits = sorted({term for term in PAYMENT_TERMS if term in payment_text})

    score = 0
    score += min(len(mobile_hits) * 2, 8)
    score += 4 if comments == 0 else 3 if comments <= MAX_PRIORITY_COMMENTS else 1
    score += min(len(payment_hits), 4)
    score -= min(len(hard_hits) * 4, 8)

    if score >= 10:
        fit = "HIGH"
    elif score >= 6:
        fit = "MEDIUM"
    else:
        fit = "LOW"

    economic_status = classify_economic_status(text, payment_hits, label_names)
    payment_status = "SIGNALS_PRESENT" if payment_hits else "UNVERIFIED"
    return {
        "score": score,
        "fit": fit,
        "economic_status": economic_status,
        "economic_priority": ECONOMIC_PRIORITY[economic_status],
        "payment_status": payment_status,
        "mobile_ai_signals": mobile_hits[:6],
        "payment_signals": payment_hits[:6],
        "hard_env_signals": hard_hits[:4],
        "label_signals": label_names[:6],
    }


def send_telegram_notification(token, chat_id, message):
    """Send a Telegram notification and report whether delivery succeeded."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            print("Telegram notification sent successfully.")
        return True
    except Exception as e:
        print(f"Failed to send Telegram notification: {e}")
        return False


def send_discord_notification(webhook_url, message):
    """Send a Discord notification and report whether delivery succeeded."""
    payload = {"content": message}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            print("Discord notification sent successfully.")
        return True
    except Exception as e:
        print(f"Failed to send Discord notification: {e}")
        return False


def create_github_issue(repo_fullname, token, title, body):
    """Create a GitHub Issue alert and report whether delivery succeeded."""
    url = f"https://api.github.com/repos/{repo_fullname}/issues"
    payload = {"title": title, "body": body}
    headers = github_headers(token)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            print("GitHub Issue notification created successfully.")
        return True
    except Exception as e:
        print(f"Failed to create GitHub Issue notification: {e}")
        return False


def sort_bounties(bounties):
    bounties.sort(
        key=lambda b: (
            b["economic_priority"],
            b["score"],
            -int(b["comments"] or 0),
        ),
        reverse=True,
    )


def review_finalist_comments(new_bounties, github_token, seen_urls):
    """Review comments for a bounded number of top candidates before alerting."""
    sort_bounties(new_bounties)
    checked = 0
    retained = []

    for bounty in new_bounties:
        if (
            checked < COMMENT_REVIEW_LIMIT
            and bounty["economic_status"] in COMMENT_REVIEW_STATUSES
            and int(bounty.get("comments") or 0) > 0
        ):
            checked += 1
            comments = fetch_issue_comments(bounty.get("comments_url"), github_token)
            comment_status = classify_comment_status(comments)
            if comment_status:
                bounty["economic_status"] = comment_status
                bounty["economic_priority"] = ECONOMIC_PRIORITY[comment_status]

        if bounty["economic_status"] in SUPPRESSED_ECONOMIC_STATUSES:
            seen_urls.add(bounty["url"])
            continue
        retained.append(bounty)

    sort_bounties(retained)
    return retained


def main():
    github_token = os.environ.get("GITHUB_TOKEN")
    repo_fullname = os.environ.get("GITHUB_REPOSITORY")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")

    seen_urls = load_seen_bounties()
    new_bounties = []

    print("Scouting GitHub for active bounties...")
    for query in SEARCH_QUERIES:
        results = search_github(query, github_token)
        for item in results.get("items", []):
            url = item.get("html_url")
            if not url or url in seen_urls or any(b["url"] == url for b in new_bounties):
                continue

            candidate_repo = repo_from_issue_url(url)
            if repo_fullname and candidate_repo.lower() == repo_fullname.lower():
                seen_urls.add(url)
                continue

            if is_meta_alert(item):
                seen_urls.add(url)
                continue

            if not is_clean_candidate(item):
                continue

            classification = classify_candidate(item)
            if classification["fit"] == "LOW":
                seen_urls.add(url)
                continue
            if classification["economic_status"] in SUPPRESSED_ECONOMIC_STATUSES:
                seen_urls.add(url)
                continue

            new_bounties.append({
                "title": item.get("title"),
                "url": url,
                "repo": candidate_repo,
                "comments_url": comments_url_from_issue(item),
                "comments": item.get("comments"),
                "updated_at": item.get("updated_at"),
                **classification,
            })

    new_bounties = review_finalist_comments(new_bounties, github_token, seen_urls)

    if not new_bounties:
        print("No new bounty opportunities found.")
        save_seen_bounties(seen_urls)
        return

    print(f"Discovered {len(new_bounties)} NEW bounty opportunities!")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    notif_lines = [
        f"🎯 *New Bounty Alert* ({now_str})",
        f"Found {len(new_bounties)} new opportunity{'ies' if len(new_bounties) > 1 else ''}:\n",
    ]
    for idx, b in enumerate(new_bounties, start=1):
        notif_lines.append(f"{idx}. *{b['title']}*")
        notif_lines.append(f"   • Repository: `{b['repo']}`")
        notif_lines.append(f"   • Fit: {b['fit']} (score {b['score']})")
        notif_lines.append(f"   • Economic status: {b['economic_status']}")
        notif_lines.append(f"   • Comments: {b['comments']}")
        notif_lines.append(f"   • AI/mobile signals: {', '.join(b['mobile_ai_signals']) or 'none'}")
        notif_lines.append(f"   • Link: {b['url']}\n")
    notification_msg = "\n".join(notif_lines)

    delivered = False

    if telegram_token and telegram_chat_id:
        delivered = send_telegram_notification(
            telegram_token, telegram_chat_id, notification_msg
        ) or delivered

    if discord_webhook:
        discord_msg = notification_msg.replace("•", "-")
        delivered = send_discord_notification(discord_webhook, discord_msg) or delivered

    if github_token and repo_fullname:
        issue_title = (
            f"🎯 Bounty Alert: {len(new_bounties)} New Opportunity"
            f"{'ies' if len(new_bounties) > 1 else ''} found"
        )
        issue_body = (
            "### Active Bounty Scan Results\n\n"
            f"**Scan Time:** {now_str}\n\n"
        )
        for idx, b in enumerate(new_bounties, start=1):
            issue_body += (
                f"#### {idx}. [{b['title']}]({b['url']})\n"
                f"- **Repository:** [{b['repo']}](https://github.com/{b['repo']})\n"
                f"- **Fit:** {b['fit']} (score {b['score']})\n"
                f"- **Economic status:** {b['economic_status']}\n"
                f"- **Payment signals:** {', '.join(b['payment_signals']) or 'none'}\n"
                f"- **Labels:** {', '.join(b['label_signals']) or 'none'}\n"
                f"- **Comments:** {b['comments']}\n"
                f"- **AI/mobile signals:** {', '.join(b['mobile_ai_signals']) or 'none'}\n"
                f"- **Last Updated:** {b['updated_at']}\n\n"
            )
        delivered = create_github_issue(
            repo_fullname, github_token, issue_title, issue_body
        ) or delivered

    if delivered:
        seen_urls.update(b["url"] for b in new_bounties)
        print("Notification delivered; candidates marked as seen.")
    else:
        print("No notification channel delivered; candidates remain unseen for retry.")

    save_seen_bounties(seen_urls)
    print("State saved successfully.")


if __name__ == "__main__":
    main()

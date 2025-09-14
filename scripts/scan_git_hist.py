from email import message_from_string
import subprocess


def get_git_commits():
    # Get commit hashes
    result = subprocess.run(
        ["git", "log", "--pretty=format:%H"],
        capture_output=True,
        text=True,
        check=True,
    )
    hashes = result.stdout.strip().split("\n")
    commit_data = []
    last_date = None

    for h in hashes:
        # Get commit metadata
        meta = subprocess.run(
            ["git", "show", "-s", "--format=%an|%ad|%s", h, "--date=short"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        author, date, message = meta.split("|", 2)

        # Get diff stats
        diff = subprocess.run(
            ["git", "show", "--stat", "--oneline", h],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

        added = deleted = 0
        for line in diff.splitlines():
            if "insertion" in line or "deletion" in line:
                # Example line: " 3 files changed, 10 insertions(+), 5 deletions(-)"
                if "insertion" in line:
                    parts = line.split(",")
                    for part in parts:
                        if "insertion" in part:
                            added += int(part.strip().split()[0])
                        elif "deletion" in part:
                            deleted += int(part.strip().split()[0])

        if last_date == date:
            last = commit_data[-1]
            last["hash"] += " | " + author
            last["message"] += " | " + message
            last["added"] += added
            last["deleted"] += deleted
            last["changed"] += added + deleted
        else:
            commit_data.append(
                {
                    "hash": h,
                    "author": author,
                    "date": date,
                    "message": message,
                    "added": added,
                    "deleted": deleted,
                    "changed": added + deleted,
                }
            )

        last_date = date

    return commit_data


def print_commits(commits):
    for c in commits:
        print(f"{c['date']}\t| {c['changed'] // 60}:{c['changed'] % 60}\t| {c['message']}")


if __name__ == "__main__":
    commits = get_git_commits()
    print_commits(commits)

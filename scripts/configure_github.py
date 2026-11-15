"""Apply reproducible repository protections after the initial master CI run succeeds."""

import argparse
import json
import re
import subprocess


def api(method, path, payload=None):
    result = subprocess.run(
        ["gh", "api", "--method", method, path, *(["--input", "-"] if payload is not None else [])],
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def configure(repo):
    root = f"repos/{repo}"
    api(
        "PATCH",
        root,
        {
            "default_branch": "master",
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
            "has_wiki": False,
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            },
        },
    )
    api("PUT", f"{root}/vulnerability-alerts")
    api("PUT", f"{root}/automated-security-fixes")
    api("PUT", f"{root}/private-vulnerability-reporting")
    api(
        "PUT",
        f"{root}/actions/permissions/workflow",
        {
            "default_workflow_permissions": "read",
            "can_approve_pull_request_reviews": False,
        },
    )
    api("PUT", f"{root}/actions/permissions", {"enabled": True, "allowed_actions": "selected"})
    api(
        "PUT",
        f"{root}/actions/permissions/selected-actions",
        {
            "github_owned_allowed": True,
            "verified_allowed": False,
            "patterns_allowed": ["github/codeql-action/*"],
        },
    )
    rule = {
        "name": "Reviewed changes only · master and main",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {
                "include": ["~DEFAULT_BRANCH", "refs/heads/master", "refs/heads/main"],
                "exclude": [],
            }
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
            {
                "type": "pull_request",
                "parameters": {
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": True,
                    "required_approving_review_count": 1,
                    "required_review_thread_resolution": True,
                    "allowed_merge_methods": ["squash"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": context, "integration_id": 15368}
                        for context in [
                            "Quality",
                            "Tests (3.12)",
                            "MySQL concurrency",
                            "Analyze Python",
                        ]
                    ],
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                },
            },
        ],
    }
    existing = next((r for r in api("GET", f"{root}/rulesets") if r["name"] == rule["name"]), None)
    saved = api(
        "PUT" if existing else "POST",
        f"{root}/rulesets/{existing['id']}" if existing else f"{root}/rulesets",
        rule,
    )
    print(f"Repository safeguards enabled; active ruleset {saved['id']} has no bypass actors.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", help="owner/repository")
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
        parser.error("Pass an owner/repository slug.")
    configure(args.repository)

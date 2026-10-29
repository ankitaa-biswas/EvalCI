"""Resolve the checked-out candidate and an explicit immutable baseline policy."""
import json
import os
from pathlib import Path
import re
import subprocess


def choose_baseline(event_name, event, pinned, default_sha, parent_sha):
    if pinned: return pinned,"repository_pinned_sha"
    if event_name == "pull_request": return event["pull_request"]["base"]["sha"],"pull_request_base_sha"
    if event_name == "push":
        before = event.get("before","")
        if before and before != "0"*40: return before,"push_before_sha"
        if default_sha: return default_sha,"new_branch_default_sha"
        raise ValueError("New repository/branch requires an explicit EVALCI_BASELINE_SHA")
    explicit = event.get("inputs",{}).get("baseline_sha")
    if explicit: return explicit,"manual_explicit_sha"
    if parent_sha: return parent_sha,"manual_parent_sha"
    raise ValueError("Manual evaluation requires baseline_sha")


def git(*args):
    result = subprocess.run(["git",*args],text=True,capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    candidate = git("rev-parse","HEAD")
    if candidate != os.environ["GITHUB_SHA"]: raise ValueError("Checkout does not match the triggering revision")
    default = event["repository"]["default_branch"]
    baseline,policy = choose_baseline(os.environ["GITHUB_EVENT_NAME"],event,os.getenv("EVALCI_BASELINE_SHA"),
        git("rev-parse","--verify","refs/remotes/origin/"+default),git("rev-parse","--verify","HEAD^"))
    if not re.fullmatch(r"[0-9a-f]{40}",baseline): raise ValueError("Baseline must be a full commit SHA")
    if not git("rev-parse","--verify",baseline+"^{commit}"):
        raise ValueError("Baseline commit is not present in the checkout. Fetch it or set EVALCI_BASELINE_SHA to an available reviewed commit.")
    if baseline == candidate and os.environ["GITHUB_REF"] == "refs/heads/"+default:
        raise ValueError("Initial default-branch evaluation needs a distinct reviewed baseline SHA")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as out:
        out.write(f"candidate_sha={candidate}\nbaseline_sha={baseline}\nbaseline_policy={policy}\n")
    print(json.dumps({"candidate_sha":candidate,"baseline_sha":baseline,"baseline_policy":policy,"pr_revision_policy":"test GitHub merge commit"}))


if __name__ == "__main__": main()

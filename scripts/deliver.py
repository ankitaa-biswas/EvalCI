"""Validate and retag the exact tested image; publication is explicit and protected.

Default behavior is local verification only. Fixture evidence can never authorize
publication. No staging deployment is attempted by this helper.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from core.gate import check_evaluation
from scripts.ci import digest_file, image_id, load_images


def validate_bundle(directory, sha, baseline_sha, mode):
    manifest = json.loads((directory/"manifest.json").read_text())
    if manifest.get("passed") is not True or manifest.get("dirty") is not False or manifest.get("mode") != mode or manifest.get("cleanup_error"):
        raise ValueError("Bundle must contain a clean successful completed evaluation")
    for role,expected in (("candidate",sha),("baseline",baseline_sha)):
        binding = manifest[role]
        identity = binding["runtime_identity"]
        if binding["revision_sha"] != expected or identity["revision_sha"] != expected or identity.get("source_verified") is not True:
            raise ValueError("Bundle revision identity mismatch")
        if identity["harness_revision_sha"] != sha or binding["source_sha256"] != identity["target_source_sha256"]:
            raise ValueError("Bundle source/harness identity mismatch")
    results = json.loads((directory/"candidate-results.json").read_text())
    report = json.loads((directory/"candidate-report.json").read_text())
    checked = check_evaluation(results,report,commit_sha=sha,mode=mode)
    base_results = json.loads((directory/"baseline-results.json").read_text())
    base_report = json.loads((directory/"baseline-report.json").read_text())
    base = check_evaluation(base_results,base_report,commit_sha=baseline_sha,mode=mode,allow_bootstrap=True)
    if checked.baseline_run_id != base.run_id: raise ValueError("Bundle comparison used a different baseline")
    gate = json.loads((directory/"gate.json").read_text())
    if gate.get("passed") is not True or gate.get("run_id") != str(checked.run_id) or gate.get("commit_sha") != sha:
        raise ValueError("Bundle gate evidence is inconsistent")
    harness = json.loads((directory/"harness-identity.json").read_text())
    if harness != manifest["candidate"]["runtime_identity"]:
        raise ValueError("Evaluation harness did not use the tested candidate image")
    return manifest


def publication_allowed(event, ref, default_branch, enabled, live_passed):
    return enabled == "true" and event == "push" and bool(default_branch) and ref == "refs/heads/"+default_branch and live_passed is True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--expected-sha",required=True)
    parser.add_argument("--baseline-sha",required=True)
    parser.add_argument("--live-evidence",type=Path)
    parser.add_argument("--allow-fixture",action="store_true",help="Local verification only; cannot publish")
    parser.add_argument("--publish",action="store_true")
    parser.add_argument("--image-name",default="evalci-delivery-verification")
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}",args.expected_sha) or not re.fullmatch(r"[0-9a-f]{40}",args.baseline_sha):
        parser.error("Full candidate and baseline SHA values are required")
    if args.publish and args.allow_fixture: parser.error("Fixture evidence cannot authorize publication")
    try:
        manifest = validate_bundle(args.bundle,args.expected_sha,args.baseline_sha,"fixture")
        if digest_file(args.bundle/"tested-images.tar.gz") != manifest.get("archive_sha256"):
            raise ValueError("Release image archive checksum mismatch")
        live_passed = False
        if args.live_evidence:
            live = validate_bundle(args.live_evidence,args.expected_sha,args.baseline_sha,"ragas")
            if any(live[r]["image_id"] != manifest[r]["image_id"] for r in ("candidate","baseline")):
                raise ValueError("Live evaluation tested different image bytes")
            live_passed = True
        if not live_passed and not args.allow_fixture:
            raise ValueError("Live quality evidence is required; use --allow-fixture only for local verification")
        if args.publish:
            if not publication_allowed(os.getenv("GITHUB_EVENT_NAME"),os.getenv("GITHUB_REF"),os.getenv("EVALCI_DEFAULT_BRANCH"),os.getenv("EVALCI_ENABLE_DELIVERY"),live_passed):
                raise ValueError("Publication requires enabled trusted default-branch push and successful live evaluation")
            if not re.fullmatch(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+",args.image_name):
                raise ValueError("Publication target must be a canonical lowercase GHCR repository")
        loaded = load_images(args.bundle,candidate_sha=args.expected_sha,baseline_sha=args.baseline_sha)
        target = args.image_name+":sha-"+args.expected_sha
        subprocess.run(["docker","tag",loaded["candidate"]["image"],target],check=True)
        if image_id(target) != manifest["candidate"]["image_id"]: raise ValueError("Release image bytes differ from the tested image")
        evidence = {"revision_sha":args.expected_sha,"image":target,"image_id":image_id(target),"live_quality_verified":live_passed,
                    "mode":"continuous delivery" if args.publish else "local delivery verification", "published":False}
        if args.publish:
            token = os.environ["GITHUB_TOKEN"]
            subprocess.run(["docker","login","ghcr.io","--username",os.environ["GITHUB_ACTOR"],"--password-stdin"],input=token,text=True,check=True)
            try:
                subprocess.run(["docker","push",target],check=True)
                evidence["published"] = True
            finally:
                subprocess.run(["docker","logout","ghcr.io"],check=False)
        if args.output: args.output.write_text(json.dumps(evidence,indent=2)+"\n")
        print(json.dumps(evidence,indent=2))
        return 0
    except Exception as exc:
        print(f"Delivery blocked: {exc}")
        return 1


if __name__ == "__main__": raise SystemExit(main())

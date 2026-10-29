"""Prove CI decisions with real temporary Git revisions and real containers.

No remote repository is modified; no image is pushed. Fixture scores verify an
explicit copy-first-passage contract and are not live RAG quality measurements.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def command(args, *, cwd, log=None, expected=0):
    result=subprocess.run([str(a) for a in args],cwd=cwd,stdout=log,stderr=subprocess.STDOUT if log else None)
    if result.returncode != expected: raise RuntimeError(f"Unexpected exit {result.returncode}, expected {expected}: {args[:4]}")


def git(root,*args):return subprocess.check_output(["git",*args],cwd=root,text=True).strip()


def snapshot(source,destination):
    files=subprocess.check_output(["git","ls-files","--cached","--others","--exclude-standard","-z"],cwd=source).decode().split("\0")
    for relative in files:
        if not relative:continue
        path=source/relative
        if path.is_symlink():raise ValueError("Verification snapshot refuses symlinks")
        if path.is_file():
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    git(destination,"init","--initial-branch=main")
    git(destination,"config","user.name","EvalCI Local Verification")
    git(destination,"config","user.email","evalci-verification@example.invalid")
    git(destination,"config","commit.gpgsign","false")
    git(destination,"config","core.hooksPath","/dev/null")
    git(destination,"add","-A")
    git(destination,"commit","-m","Synthetic verification baseline; no live model claims")
    base=git(destination,"rev-parse","HEAD")
    git(destination,"commit","--allow-empty","-m","Healthy verification revision")
    return base,git(destination,"rev-parse","HEAD")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path("artifacts/cycle3"))
    args=parser.parse_args()
    root=Path.cwd().resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    python=Path(sys.executable).absolute()
    repository=Path(tempfile.mkdtemp(prefix="evalci-cycle3-revisions-"))
    summary={"mode":"fixture","claim":"synthetic contract verification; not live LLM quality or attribution accuracy","temporary_repository":str(repository)}
    try:
        baseline,healthy=snapshot(root,repository)
        def scenario(name,sha,expected=0,endpoint=None,archive=True):
            target=out/name
            if target.exists():raise ValueError("Use a new output directory for each verification run")
            args=[python,"-m","scripts.ci","--baseline-sha",baseline,"--candidate-sha",sha,"--mode","fixture","--output",target]
            if archive:args.append("--archive-images")
            if endpoint:args += ["--candidate-endpoint",endpoint]
            with (out/f"{name}.log").open("w") as log:command(args,cwd=repository,log=log,expected=expected)
            manifest=json.loads((target/"manifest.json").read_text())
            summary[name]={"revision_sha":sha,"passed":manifest["passed"],"candidate_source_sha256":manifest["candidate"]["source_sha256"],"image_id":manifest["candidate"].get("image_id")}
            return manifest
        healthy_manifest=scenario("healthy",healthy)
        assert healthy_manifest["passed"] and (out/"healthy/tested-images.tar.gz").is_file()
        with (out/"local-delivery.log").open("w") as log:
            command([python,"-m","scripts.deliver","--bundle",out/"healthy","--expected-sha",healthy,"--baseline-sha",baseline,
                "--allow-fixture","--output",out/"local-delivery.json"],cwd=repository,log=log)
        delivered=json.loads((out/"local-delivery.json").read_text())
        assert delivered["image_id"]==healthy_manifest["candidate"]["image_id"] and delivered["published"] is False
        source=repository/"rag_target/service.py"
        content=source.read_text()
        old='answer = contexts[0] if contexts else "I don\'t know."'
        if content.count(old)!=1:raise ValueError("Fixture target changed; update the explicit degradation patch")
        source.write_text(content.replace(old,'answer = contexts[-1] if contexts else "I don\'t know."'))
        git(repository,"add","rag_target/service.py");git(repository,"commit","-m","Intentional generator contract regression")
        degraded=git(repository,"rev-parse","HEAD")
        failed=scenario("degraded",degraded,expected=1)
        report=json.loads((out/"degraded/candidate-report.json").read_text())
        assert report["quality_passed"] is True and any(r["severity"]=="HIGH" and r["attributed_to"]=="generator" for r in report["regressions"])
        assert not failed["passed"] and not (out/"degraded/tested-images.tar.gz").exists()
        assert healthy_manifest["candidate"]["source_sha256"]!=failed["candidate"]["source_sha256"]
        with (out/"blocked-delivery.log").open("w") as log:
            command([python,"-m","scripts.deliver","--bundle",out/"degraded","--expected-sha",degraded,"--baseline-sha",baseline,"--allow-fixture"],cwd=repository,log=log,expected=1)
        git(repository,"checkout","--detach",healthy)
        outage=scenario("evaluation-error",healthy,expected=1,endpoint="http://rag-target:1/query")
        detail=json.loads((out/"evaluation-error/candidate-results.json").read_text())
        assert detail["status"]=="failed" and detail["completion"]["failed"]==50 and not outage["passed"]
        assert not (out/"evaluation-error/tested-images.tar.gz").exists()
        summary.update(baseline_sha=baseline,healthy_passes=True,changed_revision_high_fails=True,
            evaluation_error_fails=True,failed_runs_cannot_deliver=True,tested_image_handoff_verified=True,
            external_publication_performed=False)
        (out/"verification.json").write_text(json.dumps(summary,indent=2)+"\n")
        print(json.dumps(summary,indent=2))
        return 0
    finally:
        # Preserve the synthetic verification Git objects for independent
        # inspection. Only this safe snapshot's history is bundled.
        if (repository/".git").exists():
            subprocess.run(["git","bundle","create",str(out/"verification-revisions.bundle"),"--all"],cwd=repository,check=True)
        shutil.rmtree(repository)


if __name__ == "__main__":raise SystemExit(main())

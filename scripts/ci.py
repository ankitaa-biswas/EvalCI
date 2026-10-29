"""Build and evaluate exact Git RAG trees in disposable Compose resources.

Host dependencies: Python standard library, Git, Docker Compose >=2.24.4.
No push, registry publication, or external deployment is performed here.
"""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from scripts.image_identity import source_digest


def run(args, *, cwd=None, capture=False, check=True):
    return subprocess.run([str(a) for a in args],cwd=cwd,text=True,
        stdout=subprocess.PIPE if capture else None,check=check)


def resolve_sha(root, value):
    if not re.fullmatch(r"[0-9a-f]{40}",value): raise ValueError("Expected a full 40-character commit SHA")
    run(["git","cat-file","-e",value+"^{commit}"],cwd=root,capture=True)
    return value


def export_target(root, sha, destination):
    raw = subprocess.check_output(["git","archive",sha,"rag_target"],cwd=root)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != "rag_target" or not (member.isfile() or member.isdir()):
                raise ValueError("Unexpected baseline archive member")
        archive.extractall(destination,filter="data")
    return source_digest(destination/"rag_target")


def digest_file(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for part in iter(lambda:source.read(1024*1024),b""):result.update(part)
    return result.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+"\n")


def image_id(image):
    return run(["docker","image","inspect",image,"--format","{{.Id}}"],capture=True).stdout.strip()


def archive_images(path, images):
    # Stream instead of buffering the image archive in memory.
    with Path(path).open("wb") as target:
        process = subprocess.Popen(["docker","save",*images],stdout=subprocess.PIPE)
        try:
            with gzip.GzipFile(fileobj=target,mode="wb",mtime=0) as compressed:
                shutil.copyfileobj(process.stdout,compressed)
        finally:
            process.stdout.close()
        if process.wait() != 0: raise RuntimeError("Could not save tested images")


def load_images(directory, *, candidate_sha, baseline_sha):
    manifest = json.loads((directory/"manifest.json").read_text())
    if manifest.get("passed") is not True or manifest.get("dirty") is not False:
        raise ValueError("Only a complete clean successful image manifest can be reused")
    if manifest["candidate"]["revision_sha"] != candidate_sha or manifest["baseline"]["revision_sha"] != baseline_sha:
        raise ValueError("Image bundle belongs to different revisions")
    archive = directory/"tested-images.tar.gz"
    if digest_file(archive) != manifest.get("archive_sha256"): raise ValueError("Tested image archive checksum mismatch")
    with gzip.open(archive,"rb") as source:
        process = subprocess.run(["docker","load"],stdin=source,check=True)
    for role in ("candidate","baseline"):
        if image_id(manifest[role]["image"]) != manifest[role]["image_id"]:
            raise ValueError("Loaded image identity mismatch")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sha",required=True)
    parser.add_argument("--candidate-sha",required=True)
    parser.add_argument("--mode",choices=["fixture","ragas"],default="fixture")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--project",default="evalci-ci-"+uuid.uuid4().hex[:12])
    parser.add_argument("--timeout-seconds",type=int,default=600)
    parser.add_argument("--archive-images",action="store_true")
    parser.add_argument("--reuse-images",type=Path)
    parser.add_argument("--candidate-endpoint",default="http://rag-target:8001/query")
    parser.add_argument("--allow-dirty",action="store_true",help="Local diagnostics only; prevents release/image bundle creation")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    output = args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()): raise ValueError("Use a new empty evidence directory")
    if not re.fullmatch(r"evalci-ci-[a-z0-9-]{4,50}",args.project): raise ValueError("Project must use an isolated evalci-ci- prefix")
    candidate_sha = resolve_sha(root,args.candidate_sha)
    baseline_sha = resolve_sha(root,args.baseline_sha)
    if run(["git","rev-parse","HEAD"],cwd=root,capture=True).stdout.strip() != candidate_sha:
        raise ValueError("Candidate SHA is not the checked-out revision")
    dirty = bool(run(["git","status","--porcelain"],cwd=root,capture=True).stdout.strip())
    if dirty and not args.allow_dirty: raise ValueError("Checkout is dirty; refusing to claim exact-revision evaluation")
    if dirty and args.archive_images: raise ValueError("Dirty worktrees cannot produce release image bundles")
    manifest = {"schema_version":1,"passed":False,"mode":args.mode,"dirty":dirty,"project":args.project,
        "baseline_policy":"explicit Git revision; bootstrap absolute quality required", "timeout_seconds":args.timeout_seconds,
        "candidate":{"revision_sha":candidate_sha},"baseline":{"revision_sha":baseline_sha}}
    stage = Path(tempfile.mkdtemp(prefix=".evalci-build-",dir=root))
    private = Path(tempfile.mkdtemp(prefix="evalci-ci-private-"))
    env_file = private/"compose.env"
    compose = ["docker","compose","--project-name",args.project,"--env-file",str(env_file),"-f",str(root/"docker-compose.yml")]
    if args.mode == "fixture": compose += ["-f",str(root/"docker-compose.fixture.yml")]
    compose += ["-f",str(root/"docker-compose.ci.yml")]
    if args.mode == "fixture": compose += ["-f",str(root/"docker-compose.ci-fixture.yml")]
    started = False
    cleanup_failed = False
    own_images = []
    try:
        manifest["candidate"]["source_sha256"] = source_digest(root/"rag_target")
        manifest["baseline"]["source_sha256"] = export_target(root,baseline_sha,stage)
        token = args.project.replace("evalci-ci-","")
        for role in ("candidate","baseline"):
            manifest[role]["image"] = f"evalci-ci-{token}:{role}"
        if args.reuse_images:
            old = load_images(args.reuse_images.resolve(),candidate_sha=candidate_sha,baseline_sha=baseline_sha)
            for role in ("candidate","baseline"):
                if old[role]["source_sha256"] != manifest[role]["source_sha256"]:
                    raise ValueError("Archived target source differs from selected Git tree")
                manifest[role].update(image=old[role]["image"],image_id=old[role]["image_id"])
        else:
            for role in ("candidate","baseline"):
                image = manifest[role]["image"]
                command = ["docker","build","--tag",image,"--build-arg",f"REVISION_SHA={manifest[role]['revision_sha']}",
                    "--build-arg",f"HARNESS_REVISION_SHA={candidate_sha}","--build-arg",f"TARGET_SOURCE_SHA256={manifest[role]['source_sha256']}"]
                if role == "baseline": command += ["--build-arg",f"RAG_SOURCE_DIR={stage.name}/rag_target"]
                run([*command,"."],cwd=root)
                manifest[role]["image_id"] = image_id(image)
                own_images.append(image)
        values = {"POSTGRES_PASSWORD":secrets.token_hex(24),"EVALCI_API_KEYS":secrets.token_hex(24),
            "EVALCI_IMAGE":manifest["candidate"]["image"],"BASELINE_IMAGE":manifest["baseline"]["image"],
            "EVAL_SCORING_MODE":args.mode,"RAG_MODE":"fixture" if args.mode == "fixture" else "live",
            "EVAL_TASK_SOFT_LIMIT_SECONDS":str(args.timeout_seconds),"EVAL_TASK_TIME_LIMIT_SECONDS":str(args.timeout_seconds+60)}
        if args.mode == "ragas":
            for name in ("OPENAI_API_KEY","EVAL_JUDGE_MODEL","EVAL_EMBEDDING_MODEL","GOOGLE_API_KEY","GEMINI_GENERATION_MODEL","GEMINI_EMBEDDING_MODEL"):
                if not os.getenv(name): raise ValueError(f"Live evaluation requires {name}")
                values[name] = os.environ[name]
            if os.getenv("OPENAI_BASE_URL"): values["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
        # Single-quoted Compose dotenv values suppress interpolation. No shell
        # evaluates provider keys or model settings.
        env_file.write_text("\n".join(k+"='"+str(v).replace("'","\\'")+"'" for k,v in values.items())+"\n")
        env_file.chmod(0o600)
        if run([*compose,"ps","-q"],cwd=root,capture=True).stdout.strip(): raise ValueError("Project already has containers; choose a fresh isolated name")
        started = True
        run([*compose,"up","-d","--wait","--wait-timeout","240","--no-build"],cwd=root)
        for role,service in (("candidate","rag-target"),("baseline","rag-baseline")):
            identity = json.loads(run([*compose,"exec","-T",service,"python","-m","scripts.image_identity"],cwd=root,capture=True).stdout)
            if identity["revision_sha"] != manifest[role]["revision_sha"] or identity["target_source_sha256"] != manifest[role]["source_sha256"] or not identity["source_verified"]:
                raise ValueError("Running RAG source does not match selected Git revision")
            container = run([*compose,"ps","-q",service],cwd=root,capture=True).stdout.strip()
            if run(["docker","inspect",container,"--format","{{.Image}}"],capture=True).stdout.strip() != manifest[role]["image_id"]:
                raise ValueError("Running RAG container uses an unexpected image")
            manifest[role]["runtime_identity"] = identity
        result = run([*compose,"exec","-T","api","python","-m","scripts.ci_evaluate","--baseline-sha",baseline_sha,
            "--candidate-sha",candidate_sha,"--mode",args.mode,"--timeout-seconds",str(args.timeout_seconds),
            "--candidate-endpoint",args.candidate_endpoint],cwd=root,check=False)
        run([*compose,"cp","api:/tmp/ci-results/.",str(output)],cwd=root)
        gate = json.loads((output/"gate.json").read_text())
        if result.returncode or gate.get("passed") is not True: raise ValueError(gate.get("error","Evaluation client failed"))
        if gate.get("commit_sha") != candidate_sha or gate.get("mode") != args.mode: raise ValueError("Gate result identity mismatch")
        manifest["passed"] = True
        if args.archive_images:
            archive = output/"tested-images.tar.gz"
            archive_images(archive,[manifest[r]["image"] for r in ("candidate","baseline")])
            manifest["archive_sha256"] = digest_file(archive)
        return 0
    except Exception as exc:
        manifest.update(passed=False,error=str(exc),error_type=type(exc).__name__)
        print(f"CI regression evaluation FAILED: {exc}")
        return 1
    finally:
        if started:
            with (output/"compose.log").open("w") as log:
                subprocess.run([*compose,"logs","--no-color","--tail","300"],cwd=root,stdout=log,stderr=subprocess.STDOUT)
            cleanup = run([*compose,"down","--volumes","--remove-orphans"],cwd=root,check=False)
            if cleanup.returncode:
                cleanup_failed = True
                manifest.update(passed=False,cleanup_error="Project cleanup failed; inspect isolated resources")
        write_json(output/"manifest.json",manifest)
        shutil.rmtree(stage)
        shutil.rmtree(private)
        if cleanup_failed: return 1


if __name__ == "__main__": raise SystemExit(main())

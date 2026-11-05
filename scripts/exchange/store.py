"""Compute exchange storage: one interface over a local/shared directory and a PRIVATE Hugging Face repo.

Serves: Nick's compute exchange (1:15 AM Nov 5): token shards and checkpoints live in one place every site reads.
Owner: lane 1. Plan: docs/plans/2026-11-05-compute-exchange.md.

    store = open_store("hf:BouncySlime1215/rz-shards", kind="dataset")   # private HF dataset (needs HF_TOKEN)
    store = open_store("/kaggle/input/rz-shards")                         # Kaggle mirror / local dir

Secrets: the HF token is read from the HF_TOKEN environment variable (Colab/Kaggle secret, SLURM env), which Nick adds
himself. It is never printed, logged or written. A repo this code creates is always private, and every file pushed
through it carries the personal-use tag in the commit message.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

PERSONAL = "personal-use, not for commercial release"


class LocalStore:
    """A directory (Kaggle dataset mount, Drive, SLURM scratch, a test tmpdir)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def put(self, local: Path, remote: str, message: str = "") -> None:
        dst = self.root / remote
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copyfile(local, tmp)
        os.replace(tmp, dst)

    def get(self, remote: str, local: Path) -> Path | None:
        src = self.root / remote
        if not src.exists():
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, local)
        return local

    def list(self, prefix: str = "") -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
                      and not p.name.endswith(".tmp"))

    def exists(self, remote: str) -> bool:
        return (self.root / remote).exists()


class HubStore:
    """A private Hugging Face repo (dataset for shards, model for checkpoints)."""

    def __init__(self, repo_id: str, kind: str = "dataset"):
        from huggingface_hub import HfApi
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise SystemExit("HF_TOKEN is not set: Nick adds it as a secret (Colab/Kaggle/cluster env)")
        self.api, self.repo, self.kind = HfApi(token=token), repo_id, kind
        self.api.create_repo(repo_id, repo_type=kind, private=True, exist_ok=True)
        info = self.api.repo_info(repo_id, repo_type=kind)
        if not getattr(info, "private", True):
            raise SystemExit(f"{repo_id} is public: refusing to push personal-use data")

    def put(self, local: Path, remote: str, message: str = "") -> None:
        self.api.upload_file(path_or_fileobj=str(local), path_in_repo=remote, repo_id=self.repo, repo_type=self.kind,
                             commit_message=f"{message or remote} [{PERSONAL}]")

    def get(self, remote: str, local: Path) -> Path | None:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import EntryNotFoundError
        try:
            p = hf_hub_download(self.repo, remote, repo_type=self.kind, token=os.environ.get("HF_TOKEN"))
        except EntryNotFoundError:
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, local)
        return local

    def list(self, prefix: str = "") -> list[str]:
        return sorted(f for f in self.api.list_repo_files(self.repo, repo_type=self.kind) if f.startswith(prefix))

    def exists(self, remote: str) -> bool:
        return remote in self.list(remote)


def open_store(spec: str, kind: str = "dataset"):
    """'hf:<owner>/<name>' -> HubStore; anything else is a directory."""
    return HubStore(spec[3:], kind) if spec.startswith("hf:") else LocalStore(spec)

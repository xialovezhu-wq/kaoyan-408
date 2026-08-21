#!/usr/bin/env python3
"""408 正式入库共享的仓库级 ``flock``。

锁位置只由 canonical repo root 决定，不跟随可配置的 queue/WAL
runtime root。这样 batch writer、WAL recovery 以及 coordinator generation
takeover 可以共用同一把正式仓库互斥锁。

本模块保持轻量，不导入 queue 或 batch，避免双向依赖。上层可通过
``timeout_error`` 把超时映射为自己的领域异常。
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import time
from pathlib import Path
from typing import Type


class RepoLockTimeout(RuntimeError):
    """共享仓库锁的默认超时异常。"""


def repo_lock_path(repo_root: str | Path) -> Path:
    """返回某个 canonical repo 唯一的正式入库锁路径。"""
    root = Path(repo_root).expanduser().resolve()
    git_dir = root / ".git"
    if git_dir.is_dir():
        return git_dir / "codex-intake-408.lock"
    return root / ".codex-intake-408.lock"


class RepoLock:
    """每个 repo 唯一的非阻塞轮询 ``flock``。

    ``runtime_root`` 仅为兼容旧 batch 调用签名而保留，不参与锁路径
    计算；否则两个调用者可以更换 runtime 绕过正式互斥。
    """

    def __init__(
        self,
        repo_root: str | Path,
        runtime_root: str | Path | None = None,
        timeout: float = 30.0,
        *,
        timeout_error: Type[Exception] = RepoLockTimeout,
    ) -> None:
        del runtime_root
        self.path = repo_lock_path(repo_root)
        self.timeout = timeout
        self.timeout_error = timeout_error
        self._fh = None

    def __enter__(self) -> "RepoLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+")
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fh.seek(0)
                    self._fh.truncate()
                    acquired = dt.datetime.now(dt.timezone.utc).isoformat()
                    self._fh.write(f"pid={os.getpid()} acquired={acquired}\n")
                    self._fh.flush()
                    return self
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise self.timeout_error(
                            f"仓库入库锁等待超过 {self.timeout:g}s：{self.path}"
                        ) from exc
                    time.sleep(0.05)
        except BaseException:
            self._fh.close()
            self._fh = None
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None

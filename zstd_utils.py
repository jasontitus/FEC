"""Shared utilities for zstd compression/decompression via CLI subprocess."""

import io
import os
import glob
import subprocess


def open_readable(path, encoding="utf-8", errors="replace", null_clean=False):
    """Open a file for reading, transparently decompressing .zst if needed.

    Checks for a .zst version first. If found, pipes through `zstd -dc`.
    Falls back to the uncompressed file if it exists.
    Returns a text-mode file-like object.

    Args:
        path: Path to the file (without .zst extension).
        encoding: Text encoding for the file.
        errors: Error handling for decoding.
        null_clean: If True, strip null bytes from lines (needed for CA TSV files).
    """
    zst_path = path + ".zst"

    if os.path.exists(zst_path):
        proc = subprocess.Popen(
            ["zstd", "-dc", zst_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        raw_stream = proc.stdout
    elif os.path.exists(path):
        if null_clean:
            return _NullCleanWrapper(open(path, "r", encoding=encoding, errors=errors))
        return open(path, "r", encoding=encoding, errors=errors)
    else:
        raise FileNotFoundError(f"Neither {path} nor {zst_path} exists")

    text_stream = io.TextIOWrapper(raw_stream, encoding=encoding, errors=errors)

    if null_clean:
        return _NullCleanWrapper(text_stream, proc=proc)

    # Attach proc so callers can wait on it if needed
    text_stream._zstd_proc = proc
    return text_stream


class _NullCleanWrapper:
    """Iterator wrapper that strips null bytes from each line."""

    def __init__(self, fileobj, proc=None):
        self._fileobj = fileobj
        self._zstd_proc = proc

    def __iter__(self):
        for line in self._fileobj:
            yield line.replace("\0", "")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fileobj.close()
        if self._zstd_proc:
            self._zstd_proc.wait()

    def close(self):
        self._fileobj.close()
        if self._zstd_proc:
            self._zstd_proc.wait()

    def read(self, *args, **kwargs):
        data = self._fileobj.read(*args, **kwargs)
        return data.replace("\0", "")

    def readline(self, *args, **kwargs):
        line = self._fileobj.readline(*args, **kwargs)
        return line.replace("\0", "")


def compress_and_remove(path, logger=None):
    """Compress a file with zstd and remove the original.

    Args:
        path: Path to the file to compress.
        logger: Optional logger for progress messages.
    """
    if not os.path.exists(path):
        return

    zst_path = path + ".zst"
    if logger:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        logger.info(f"Compressing {path} ({size_mb:.0f}MB)...")

    result = subprocess.run(
        ["zstd", "-f", "--rm", path, "-o", zst_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        if logger:
            logger.error(f"zstd compression failed for {path}: {result.stderr}")
        return

    if logger:
        if os.path.exists(zst_path):
            zst_mb = os.path.getsize(zst_path) / (1024 * 1024)
            logger.info(f"Compressed to {zst_path} ({zst_mb:.0f}MB)")


def compress_existing_files(directory, patterns, logger=None):
    """Scan a directory for uncompressed files and compress them.

    Args:
        directory: Directory to scan.
        patterns: List of glob patterns (e.g. ["*.txt", "*.TSV"]).
        logger: Optional logger for progress messages.
    """
    if not os.path.isdir(directory):
        return

    for pattern in patterns:
        for filepath in glob.glob(os.path.join(directory, pattern)):
            # Skip if .zst already exists and original is still around
            compress_and_remove(filepath, logger)

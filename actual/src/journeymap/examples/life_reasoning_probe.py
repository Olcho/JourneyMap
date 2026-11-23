"""Offline corpus preparation, synthetic dry run and data-only re-evaluation."""

import argparse
from pathlib import Path

from journeymap.adapters.llm import strict_json
from journeymap.core.canonical import JsonObject, canonical_json, state_digest
from journeymap.experiments.life_reasoning_probe import (
    VERSION,
    evaluate,
    mock_packets,
    prepare,
)


def read_object(path: Path) -> JsonObject:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("probe file exceeds 64 MiB")
    value = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("probe file must contain an object")
    return value


def save(directory: Path, files: dict[str, JsonObject]) -> None:
    # Never overwrite a previous corpus, response set or report.
    encoded = {name: canonical_json(data) + "\n" for name, data in files.items()}
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in encoded.items():
        with (directory / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "mock"):
        item = sub.add_parser(command)
        item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("evaluate")
    item.add_argument("--corpus", type=Path, required=True)
    item.add_argument("--packets", type=Path, required=True)
    item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("audit")
    item.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "audit":
        corpus = read_object(args.directory / "corpus.json")
        packets = read_object(args.directory / "packets.json")
        saved = read_object(args.directory / "result.json")
        rebuilt = evaluate(corpus, packets)
        if canonical_json(saved) != canonical_json(rebuilt):
            raise ValueError("saved evaluation differs from data-only re-evaluation")
        print(
            canonical_json(
                {
                    "data_only_equal": True,
                    "result_digest": state_digest(rebuilt),
                    "live_evidence_verified": False,
                }
            )
        )
        return
    if args.output.exists():
        parser.error("output already exists; use a new directory")
    if args.command == "evaluate":
        corpus, packets = read_object(args.corpus), read_object(args.packets)
    else:
        corpus = prepare()
        packets = (
            mock_packets(corpus)
            if args.command == "mock"
            else {
                "version": VERSION,
                "corpus_digest": corpus["corpus_digest"],
                "evidence_kind": "imported-unverified",
                "records": [],
            }
        )
    files = {"corpus.json": corpus, "packets.json": packets}
    if args.command != "prepare":
        files["result.json"] = evaluate(corpus, packets)
    save(args.output, files)
    print(
        canonical_json(
            {
                "output": str(args.output),
                "version": VERSION,
                "authorized": False,
                "network_calls": 0,
                "evidence_kind": packets["evidence_kind"],
            }
        )
    )


if __name__ == "__main__":
    main()

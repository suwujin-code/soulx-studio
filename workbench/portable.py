"""Safe portable-bundle CLI for moving a workspace between Macs."""

from __future__ import annotations

import argparse

from .store import WorkbenchStore


def main() -> None:
    parser = argparse.ArgumentParser(description="SoulX 工作台跨 Mac 迁移工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export", help="导出完整便携迁移包")
    export_parser.add_argument("--output")
    restore_parser = subparsers.add_parser("restore", help="恢复到一个新的空数据目录")
    restore_parser.add_argument("bundle")
    restore_parser.add_argument("--destination", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="只检查迁移包，不写入")
    inspect_parser.add_argument("bundle")
    args = parser.parse_args()

    if args.command == "export":
        path = WorkbenchStore().export_portable_bundle(args.output)
        print(path)
    elif args.command == "restore":
        store = WorkbenchStore.restore_portable_bundle(args.bundle, args.destination)
        print(store.data_dir)
    else:
        print(WorkbenchStore.inspect_portable_bundle(args.bundle))


if __name__ == "__main__":
    main()

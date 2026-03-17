import argparse
import csv
import logging
from logging import getLogger, Logger
from pathlib import Path

from .common import ErrorCaptureHandler, process_changed_documents
from .ingest_local import get_local_documents, local_downloader
from .ingest_opentext import OpenTextDownloader


def get_console_logger(verbosity: int) -> Logger:
    """Returns a logger object set to the provided level of verbosity (0 for warn, 1 for info, 2 for debug)."""
    LOGGING_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging_level = LOGGING_LEVELS[min(verbosity, len(LOGGING_LEVELS) - 1)]  # cap to last level index
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging_level)
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] %(message)s")
    console_handler.setFormatter(formatter)
    logger = getLogger("snowdoc")
    logger.setLevel(logging_level)
    logger.addHandler(console_handler)
    logger.addHandler(ErrorCaptureHandler())
    return logger


def main() -> None:
    # Common options shared by all subcommands
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--snowflake-connection", default="default", help="Name of Snowflake connection to use (default: default)"
    )
    common.add_argument(
        "-d",
        "--delete-missing",
        action="store_true",
        help="Set to delete Snowflake documents matching the prerix that are not in the source. The default behavior is to add and update only.",
    )
    common.add_argument(
        "-u",
        "--update-display-names",
        action="store_true",
        help="Set to update the display names of already-present documents. The default behavior is to not update the display names.",
    )
    common.add_argument(
        "-v",
        "--verbose",
        help="Be verbose. Include once for INFO output, twice for DEBUG output.",
        action="count",
        default=0,
    )
    common.add_argument(
        "-o",
        "--output-csv",
        default=None,
        help="Path to write a CSV file of document changes. If omitted, no CSV is written.",
    )

    parser = argparse.ArgumentParser(
        prog="snowdoc",
        description="snowdoc - ingest documents into Snowflake.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- ingest-local ---
    local_parser = subparsers.add_parser(
        "ingest-local",
        parents=[common],
        help="Ingest local documents into Snowflake.",
    )
    local_parser.add_argument("root_dir", help="Root directory containing documents")
    local_parser.add_argument(
        "-n",
        "--source-name",
        default="",
        help="Name of the source to insert into the URI. Placed in the 'netloc' section of the URI.",
    )

    # --- ingest-opentext ---
    opentext_parser = subparsers.add_parser(
        "ingest-opentext",
        parents=[common],
        help="Ingest OpenText documents into Snowflake.",
    )
    opentext_parser.add_argument("node_ids", nargs="+", type=int, help="OpenText node IDs to process")
    opentext_parser.add_argument(
        "-p", "--prefix", default="opentext://", help="URL prefix for the documents (default: opentext://)"
    )

    args = parser.parse_args()
    logger = get_console_logger(args.verbose)
    pcd_params = {
        "connection": args.snowflake_connection,
        "delete_missing": args.delete_missing,
        "update_display_names": args.update_display_names,
        "logger": logger,
    }

    if args.command == "ingest-local":
        root_path = Path(args.root_dir)
        logger.info(f"Getting local documents in {root_path}...")
        changes = process_changed_documents(
            get_local_documents(root_path=root_path, source_name=args.source_name),
            downloader=local_downloader,
            prefix=f"file://{args.source_name}",
            **pcd_params,
        )
    elif args.command == "ingest-opentext":
        opentext_downloader = OpenTextDownloader(logger=logger)
        logger.info(f"Getting OpenText documents in root nodes {args.node_ids}...")
        changes = process_changed_documents(
            opentext_downloader.get_opentext_documents(args.node_ids, prefix=args.prefix),
            downloader=opentext_downloader,
            prefix=args.prefix,
            **pcd_params,
        )

    if args.output_csv:
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["source_uri_base", "state", "core_changes", "metadata_changes"])
            writer.writerows(changes)
        logger.info(f"Wrote {len(changes)} changes to {args.output_csv}")


if __name__ == "__main__":
    main()

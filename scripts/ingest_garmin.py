import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Garmin export file")
    parser.add_argument("--file", required=True, help="Path to Garmin export file")
    args = parser.parse_args()
    print(f"[TODO] ingest garmin activity from: {args.file}")


if __name__ == "__main__":
    main()

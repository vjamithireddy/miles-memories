import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Google location history file")
    parser.add_argument("--file", required=True, help="Path to Google Takeout location file")
    args = parser.parse_args()
    print(f"[TODO] ingest location history from: {args.file}")


if __name__ == "__main__":
    main()

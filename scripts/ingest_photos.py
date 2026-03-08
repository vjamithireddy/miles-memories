import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Google Photos Takeout archive")
    parser.add_argument("--file", required=True, help="Path to Google Photos Takeout zip")
    args = parser.parse_args()
    print(f"[TODO] ingest photos metadata from: {args.file}")


if __name__ == "__main__":
    main()

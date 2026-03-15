import argparse

from app.bootstrap import ensure_default_user, set_work_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Set work profile for trip detection")
    parser.add_argument("--lat", type=float, required=True, help="Work latitude")
    parser.add_argument("--lon", type=float, required=True, help="Work longitude")
    parser.add_argument(
        "--local-radius-meters",
        type=int,
        default=1609,
        help="Work radius in meters for commute exclusion",
    )
    args = parser.parse_args()

    ensure_default_user()
    set_work_profile(args.lat, args.lon, args.local_radius_meters)
    print(
        "Work profile updated: "
        f"lat={args.lat} lon={args.lon} local_radius_meters={args.local_radius_meters}"
    )


if __name__ == "__main__":
    main()

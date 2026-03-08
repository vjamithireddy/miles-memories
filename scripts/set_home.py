import argparse

from app.bootstrap import ensure_default_user, set_home_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Set home profile for trip detection")
    parser.add_argument("--lat", type=float, required=True, help="Home latitude")
    parser.add_argument("--lon", type=float, required=True, help="Home longitude")
    parser.add_argument(
        "--local-radius-meters",
        type=int,
        default=16093,
        help="Local radius in meters for local-vs-travel classification",
    )
    args = parser.parse_args()

    ensure_default_user()
    set_home_profile(args.lat, args.lon, args.local_radius_meters)
    print(
        "Home profile updated: "
        f"lat={args.lat} lon={args.lon} local_radius_meters={args.local_radius_meters}"
    )


if __name__ == "__main__":
    main()

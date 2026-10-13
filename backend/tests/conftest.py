def pytest_addoption(parser):
    parser.addoption(
        "--update-inventory",
        action="store_true",
        default=False,
        help="Regenerate backend/tests/fixtures/route_inventory.json from the live app.",
    )

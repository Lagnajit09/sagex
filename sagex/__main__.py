"""Entry point so the app can be launched with:  python -m sagex

`python -m sagex` tells Python to run this file as the package's main module.
"""

from sagex.app import SagexApp


def main() -> None:
    SagexApp().run()


if __name__ == "__main__":
    main()

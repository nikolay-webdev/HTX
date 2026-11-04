"""Start the safe HTX live simulator with the Run button in VS Code."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
os.chdir(PROJECT_DIR)

from htx_bot.config import load_live_config  # noqa: E402
from htx_bot.live import LiveSimulator  # noqa: E402
from htx_bot.storage import Storage  # noqa: E402
from htx_bot.telegram import TelegramClient  # noqa: E402


def main() -> None:
    env_file = PROJECT_DIR / ".env"
    try:
        config = load_live_config(env_file)
    except (ValueError, OSError) as error:
        print("Die Simulator-Konfiguration konnte nicht geladen werden.")
        print(f"Grund: {error}")
        print(f"Bitte prüfen Sie die Datei: {env_file}")
        return

    storage = Storage(config.database_path)
    telegram = TelegramClient(config.telegram_token, config.telegram_chat_id)
    simulator = LiveSimulator(config, storage, telegram)

    try:
        simulator.run()
    finally:
        storage.close()


if __name__ == "__main__":
    main()


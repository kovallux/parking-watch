# Moving to another Mac

The repository contains the application, not your local camera setup. Move the local setup separately.

1. On the old Mac, open `Удалить из системы.command` to stop its background jobs. Use only one running Telegram handler for a bot.
2. Clone or copy the project to its final location on the new Mac. Separately transfer `config.local.json`, the required empty references and their provenance in `data/`. Transfer model weights too if the new Mac must start offline.
3. Open `Установить в систему.command` on the new Mac. Its Python environment is created locally for Apple Silicon or Intel; do not copy a Python environment between architectures.
4. Enter the camera credential and bot token in the hidden-input dialogues if they are absent from the new Mac's Keychain. The paired chat ID in the local configuration can be retained for the same bot.
5. Send `/check` to your bot and verify a fresh image and status. Test both an occupied and an empty space before relying on vacancy alerts.

Credentials are not stored in the project or transferred by Git. References must keep the original resolution and camera view. If the camera moved, recalibrate its polygons and references.

Keep the project available at its installed path. If it moves again, remove the old installation first and install from the new location.

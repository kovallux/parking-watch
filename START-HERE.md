# Setup on macOS

This guide is for a new installation. You need a fixed RTSP camera reachable from the Mac and, for background photo notifications, your own Telegram bot.

## 1. Prepare the configuration

Copy `config.example.json` to `config.local.json`. Set `camera.ip` to your camera's address. Replace the example parking polygons and names with your own. Coordinates range from 0 to 1 and must describe four corners of a convex region in boundary order.

The Keychain service/account values identify where credentials will be stored; they are not passwords. Keep passwords and bot tokens out of the JSON file. Leave `empty_references` empty until a real empty image has been checked. After checking the polygons on a real frame, set `calibrated` to `true`; this confirms the regions, not vacancy.

## 2. Prepare Python and dependencies

From the project directory:

```sh
/bin/zsh scripts/install.zsh --prepare-only
```

This prepares the matching environment under `~/Library/Application Support/ParkingWatch/venvs/`. The Intel dependency versions are pinned separately. Internet access is needed for the first installation.

If Finder will not open a `.command` file, restore its executable permission:

```sh
chmod u+x ./*.command
```

## 3. Pair your Telegram bot

Create a bot through Telegram's BotFather. Open `Подключить Telegram.command` and enter its token in the hidden-input dialogue. Follow the generated private-chat link and press Start. The token goes into Keychain; the paired chat ID and bot username go into your ignored local configuration.

Pairing starts the background jobs. Camera access is completed in the next step.

## 4. Complete installation

Open `Установить в систему.command`. Enter the camera verification code when requested. Credentials are read from Keychain on subsequent starts. The installer checks the configured camera and model and enables startup after login to macOS.

Send `/check` to your own bot. Check the actual image, time, selected regions and status. A working detector can report `unknown` until an empty reference is available; this is different from a camera connection failure.

## 5. Calibrate vacancy

Save real empty frames under `data/`. Check the whole parking region visually, use an independent empty frame, and test an occupied example as well. Add the relative reference path only to that space's `empty_references`, then open `Запустить.command` to reload the configuration.

Do not install a missed vehicle detection as an empty reference. Day and night references need separate visual confirmation. The application does not generate verified references automatically.

## Daily commands

| Command | Action |
| --- | --- |
| `Запустить.command` | Reload configuration and start background monitoring and Telegram |
| `Остановить.command` | Stop monitoring; the Telegram handler remains available |
| `Проверить работу.command` | Check service and camera freshness |
| `Получить кадр.command` | Save a real camera frame locally |
| `Удалить из системы.command` | Disable this installation and its background jobs, retaining project files and Keychain entries |

Keep the project at the installed path and available offline. Background jobs need an awake Mac, an accessible camera and the mounted project volume. Startup occurs after login, not before it.

## Retention

The example enables bounded retention of generated photos and disables candidate capture. The cleanup command previews its changes unless `--apply` is supplied:

```sh
source scripts/python.zsh
"$task_python" -m parking_watch.cli --config config.local.json cleanup
```

Configured references and their provenance are protected. Unreviewed candidate frames are also protected by the normal retention policy, so candidate capture has a separate storage cap.

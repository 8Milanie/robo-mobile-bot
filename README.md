# Robo Mobile Bot Flutter Starter

This package turns the earlier MT5 Python bot into a **real mobile controller app project**.

## What it is
- A Flutter Android app project
- A small Flask backend for license activation, bot start/stop, and config saving
- Designed to control the `mt5_trading_bot.py` desktop trading engine

## Important reality
A phone app alone cannot safely run full MT5 algorithmic trading by itself.  
The real trade execution still needs:
1. MetaTrader 5 desktop or VPS
2. The Python/MT5 bot engine
3. A backend/API the phone talks to

So this package gives you the full structure for a real mobile system:
**APK controller + backend + trading engine**

## Included files
- `lib/main.dart` — Flutter app
- `backend_api.py` — simple Flask backend
- `pubspec.yaml`
- `android/app/src/main/AndroidManifest.xml`
- `README.md`

## Build APK
1. Install Flutter
2. Run:
   ```bash
   flutter pub get
   flutter build apk --release
   ```
3. APK output:
   `build/app/outputs/flutter-apk/app-release.apk`

## Run backend
```bash
pip install flask
python backend_api.py
```

## Demo license keys
- `DEMO-1234`
- `RTB-TEST-2026`

## What to connect next
- replace demo licenses with database validation
- connect backend config into the MT5 bot `.env`
- add Telegram alerts
- add VPS hosting
- secure the API with auth tokens and HTTPS

## Note
This is a functional starter project, not a guaranteed profit bot.

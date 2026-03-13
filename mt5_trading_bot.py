"""
MT5 EMA + RSI trading bot starter
---------------------------------
Features:
- Connects to MetaTrader 5 terminal via official MetaTrader5 Python package
- Trend filter: fast EMA vs slow EMA
- Entry filter: RSI cross back through thresholds
- Risk-based position sizing
- Stop loss / take profit
- Max daily loss guard
- One open position per symbol
- Optional Telegram alerts
- DRY_RUN mode by default for safe testing

IMPORTANT:
- Test on demo first.
- This starter bot is educational code, not a profit guarantee.
"""

from __future__ import annotations

import math
import os
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    symbol: str = os.getenv("SYMBOL", "EURUSD")
    timeframe_name: str = os.getenv("TIMEFRAME", "M15")
    bars: int = int(os.getenv("BARS", "500"))

    fast_ema: int = int(os.getenv("FAST_EMA", "50"))
    slow_ema: int = int(os.getenv("SLOW_EMA", "200"))
    rsi_period: int = int(os.getenv("RSI_PERIOD", "14"))
    rsi_buy_threshold: int = int(os.getenv("RSI_BUY_THRESHOLD", "40"))
    rsi_sell_threshold: int = int(os.getenv("RSI_SELL_THRESHOLD", "60"))

    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.5"))
    reward_to_risk: float = float(os.getenv("REWARD_TO_RISK", "2.0"))
    sl_lookback: int = int(os.getenv("SL_LOOKBACK", "10"))

    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "2.0"))
    max_positions_per_symbol: int = int(os.getenv("MAX_POSITIONS_PER_SYMBOL", "1"))
    check_interval_seconds: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "15"))
    deviation: int = int(os.getenv("DEVIATION", "20"))
    magic_number: int = int(os.getenv("MAGIC_NUMBER", "20260313"))
    dry_run: bool = os.getenv("DRY_RUN", "true").strip().lower() == "true"

    mt5_login: Optional[int] = int(os.getenv("MT5_LOGIN")) if os.getenv("MT5_LOGIN") else None
    mt5_password: Optional[str] = os.getenv("MT5_PASSWORD")
    mt5_server: Optional[str] = os.getenv("MT5_SERVER")
    mt5_path: Optional[str] = os.getenv("MT5_PATH")

    telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")


class MT5TradingBot:
    def __init__(self, config: BotConfig) -> None:
        self.cfg = config
        self.timeframe = self._parse_timeframe(config.timeframe_name)
        self.last_bar_time: Optional[pd.Timestamp] = None
        self.start_equity_for_day: Optional[float] = None
        self.current_day: Optional[str] = None

    @staticmethod
    def _parse_timeframe(name: str) -> int:
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        if name not in mapping:
            raise ValueError(f"Unsupported timeframe: {name}")
        return mapping[name]

    def send_alert(self, text: str) -> None:
        print(text)
        if not (self.cfg.telegram_bot_token and self.cfg.telegram_chat_id):
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage",
                data={"chat_id": self.cfg.telegram_chat_id, "text": text},
                timeout=10,
            )
        except Exception as exc:
            print(f"Telegram alert failed: {exc}")

    def connect(self) -> None:
        kwargs = {}
        if self.cfg.mt5_path:
            kwargs["path"] = self.cfg.mt5_path
        if self.cfg.mt5_login:
            kwargs["login"] = self.cfg.mt5_login
        if self.cfg.mt5_password:
            kwargs["password"] = self.cfg.mt5_password
        if self.cfg.mt5_server:
            kwargs["server"] = self.cfg.mt5_server

        ok = mt5.initialize(**kwargs) if kwargs else mt5.initialize()
        if not ok:
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError("Could not fetch account info after MT5 initialization.")

        self.send_alert(
            f"Connected to MT5 | Login: {acc.login} | Server: {acc.server} | Balance: {acc.balance}"
        )

        symbol_info = mt5.symbol_info(self.cfg.symbol)
        if symbol_info is None:
            raise RuntimeError(f"Symbol {self.cfg.symbol} not found in MT5.")
        if not symbol_info.visible:
            if not mt5.symbol_select(self.cfg.symbol, True):
                raise RuntimeError(f"Failed to select symbol {self.cfg.symbol} in Market Watch.")

    def disconnect(self) -> None:
        mt5.shutdown()

    def fetch_data(self) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(self.cfg.symbol, self.timeframe, 0, self.cfg.bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No rates returned for {self.cfg.symbol}. MT5 error: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    @staticmethod
    def add_indicators(df: pd.DataFrame, fast: int, slow: int, rsi_period: int) -> pd.DataFrame:
        out = df.copy()
        out["ema_fast"] = out["close"].ewm(span=fast, adjust=False).mean()
        out["ema_slow"] = out["close"].ewm(span=slow, adjust=False).mean()

        delta = out["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        out["rsi"] = 100 - (100 / (1 + rs))
        out["rsi"] = out["rsi"].fillna(50)
        return out

    def is_new_bar(self, df: pd.DataFrame) -> bool:
        latest_bar_time = df.iloc[-1]["time"]
        if self.last_bar_time is None:
            self.last_bar_time = latest_bar_time
            return False
        if latest_bar_time > self.last_bar_time:
            self.last_bar_time = latest_bar_time
            return True
        return False

    def reset_daily_guard_if_needed(self) -> None:
        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError("Could not read account info.")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.current_day != today:
            self.current_day = today
            self.start_equity_for_day = float(acc.equity)
            self.send_alert(f"New trading day started. Day start equity: {self.start_equity_for_day:.2f}")

    def daily_loss_limit_hit(self) -> bool:
        self.reset_daily_guard_if_needed()
        acc = mt5.account_info()
        if acc is None or self.start_equity_for_day is None:
            return False
        loss_pct = max(0.0, (self.start_equity_for_day - float(acc.equity)) / self.start_equity_for_day * 100)
        return loss_pct >= self.cfg.max_daily_loss_pct

    def count_open_positions(self) -> int:
        positions = mt5.positions_get(symbol=self.cfg.symbol)
        return 0 if positions is None else len(positions)

    def generate_signal(self, df: pd.DataFrame) -> Optional[str]:
        # Use only closed candles for signals
        if len(df) < max(self.cfg.slow_ema + 5, self.cfg.rsi_period + 5):
            return None

        prev2 = df.iloc[-3]
        prev1 = df.iloc[-2]

        bullish_trend = prev1["ema_fast"] > prev1["ema_slow"]
        bearish_trend = prev1["ema_fast"] < prev1["ema_slow"]

        buy_cross = prev2["rsi"] < self.cfg.rsi_buy_threshold and prev1["rsi"] >= self.cfg.rsi_buy_threshold
        sell_cross = prev2["rsi"] > self.cfg.rsi_sell_threshold and prev1["rsi"] <= self.cfg.rsi_sell_threshold

        if bullish_trend and buy_cross:
            return "buy"
        if bearish_trend and sell_cross:
            return "sell"
        return None

    def get_tick(self):
        tick = mt5.symbol_info_tick(self.cfg.symbol)
        if tick is None:
            raise RuntimeError(f"Could not get tick for {self.cfg.symbol}")
        return tick

    def compute_sl_tp(self, df: pd.DataFrame, side: str, entry_price: float) -> Tuple[float, float]:
        recent = df.iloc[-(self.cfg.sl_lookback + 2):-2]
        if side == "buy":
            sl = float(recent["low"].min())
            risk = entry_price - sl
            tp = entry_price + (risk * self.cfg.reward_to_risk)
        else:
            sl = float(recent["high"].max())
            risk = sl - entry_price
            tp = entry_price - (risk * self.cfg.reward_to_risk)

        if risk <= 0:
            raise ValueError("Invalid SL/TP calculation; risk is <= 0.")
        return sl, tp

    def calculate_volume(self, entry_price: float, stop_loss: float) -> float:
        acc = mt5.account_info()
        info = mt5.symbol_info(self.cfg.symbol)

        if acc is None or info is None:
            raise RuntimeError("Account or symbol info unavailable for volume calculation.")

        risk_money = float(acc.balance) * (self.cfg.risk_per_trade_pct / 100.0)
        distance = abs(entry_price - stop_loss)
        if distance <= 0:
            raise ValueError("Stop loss distance must be positive.")

        tick_size = float(info.trade_tick_size or 0.0)
        tick_value = float(info.trade_tick_value or 0.0)
        vol_step = float(info.volume_step or 0.01)
        vol_min = float(info.volume_min or 0.01)
        vol_max = float(info.volume_max or 100.0)

        if tick_size <= 0 or tick_value <= 0:
            # conservative fallback
            raw_lots = 0.01
        else:
            value_per_price_unit_per_lot = tick_value / tick_size
            loss_per_lot = distance * value_per_price_unit_per_lot
            raw_lots = risk_money / loss_per_lot if loss_per_lot > 0 else vol_min

        stepped = math.floor(raw_lots / vol_step) * vol_step
        volume = max(vol_min, min(vol_max, stepped))
        return round(volume, 2 if vol_step >= 0.01 else 3)

    def place_order(self, side: str, sl: float, tp: float) -> None:
        tick = self.get_tick()
        entry = tick.ask if side == "buy" else tick.bid
        volume = self.calculate_volume(entry, sl)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.cfg.symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": entry,
            "sl": sl,
            "tp": tp,
            "deviation": self.cfg.deviation,
            "magic": self.cfg.magic_number,
            "comment": "EMA_RSI_Python_Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        if self.cfg.dry_run:
            self.send_alert(
                f"[DRY RUN] {side.upper()} {self.cfg.symbol} | entry={entry:.5f} sl={sl:.5f} tp={tp:.5f} vol={volume}"
            )
            return

        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send returned None. MT5 error: {mt5.last_error()}")

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"Order failed. retcode={result.retcode}, comment={result.comment}")

        self.send_alert(
            f"ORDER PLACED | {side.upper()} {self.cfg.symbol} | entry={entry:.5f} sl={sl:.5f} tp={tp:.5f} vol={volume}"
        )

    def run_once(self) -> None:
        if self.daily_loss_limit_hit():
            self.send_alert("Daily loss limit reached. Trading paused for the rest of the day.")
            return

        df = self.fetch_data()
        df = self.add_indicators(df, self.cfg.fast_ema, self.cfg.slow_ema, self.cfg.rsi_period)

        if not self.is_new_bar(df):
            return

        if self.count_open_positions() >= self.cfg.max_positions_per_symbol:
            self.send_alert(f"Skipped {self.cfg.symbol}: max open positions already reached.")
            return

        signal = self.generate_signal(df)
        if signal is None:
            self.send_alert(f"No trade signal on {self.cfg.symbol} at {df.iloc[-2]['time']}.")
            return

        tick = self.get_tick()
        entry = tick.ask if signal == "buy" else tick.bid
        sl, tp = self.compute_sl_tp(df, signal, entry)
        self.place_order(signal, sl, tp)

    def run_forever(self) -> None:
        self.connect()
        self.reset_daily_guard_if_needed()
        self.send_alert(
            f"Bot started for {self.cfg.symbol} on {self.cfg.timeframe_name} | dry_run={self.cfg.dry_run}"
        )
        try:
            while True:
                try:
                    self.run_once()
                except Exception as exc:
                    self.send_alert(f"Loop error: {exc}\n{traceback.format_exc(limit=1)}")
                time.sleep(self.cfg.check_interval_seconds)
        finally:
            self.disconnect()


def main() -> None:
    cfg = BotConfig()
    bot = MT5TradingBot(cfg)
    bot.run_forever()


if __name__ == "__main__":
    main()

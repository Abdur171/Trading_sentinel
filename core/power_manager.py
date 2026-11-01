"""
core/power_manager.py - Windows Screen Lock & Sleep Prevention Manager.
Prevents Windows Modern Standby / Sleep from suspending Sentinel daemon threads when screen is locked.
Includes monotonic clock discontinuity detection to handle sleep-resume recovery gracefully.
"""

from __future__ import annotations
import sys
import time
import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Windows kernel32 SetThreadExecutionState flags
ES_SYSTEM_REQUIRED: int = 0x00000001
ES_DISPLAY_REQUIRED: int = 0x00000002
ES_AWAYMODE_REQUIRED: int = 0x00000040
ES_CONTINUOUS: int = 0x80000000


class PowerManager:
    """
    Manages OS-level execution states and sleep-wake detection.
    On Windows:
    - Requests ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
      allowing screen lock/display power-down without suspending background worker loops.
    Cross-platform:
    - Tracks loop tick intervals to detect clock discontinuities (e.g. system wake from standby).
    """

    def __init__(self, prevent_sleep: bool = True):
        self.prevent_sleep = prevent_sleep
        self._sleep_prevention_active: bool = False
        self._last_tick: float = time.monotonic()
        self._wake_handlers: list[Callable[[float], None]] = []

    def enable_sleep_prevention(self) -> bool:
        """
        Informs Windows kernel that the Sentinel requires continuous system execution.
        Permits display sleep (screen lock) while keeping daemon processes fully awake.
        """
        if not self.prevent_sleep:
            logger.info("PowerManager: Sleep prevention disabled by configuration.")
            return False

        if sys.platform != "win32":
            logger.debug("PowerManager: Non-Windows OS detected; native Windows execution state skipped.")
            self._sleep_prevention_active = True
            return True

        try:
            import ctypes
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            res = ctypes.windll.kernel32.SetThreadExecutionState(flags)
            if res != 0:
                self._sleep_prevention_active = True
                logger.info(
                    "PowerManager: Windows sleep prevention activated "
                    "(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED). "
                    "Process will run continuously through screen lock."
                )
                return True
            else:
                logger.warning("PowerManager: SetThreadExecutionState returned 0 (failed).")
                return False
        except Exception as e:
            logger.warning(f"PowerManager: Error requesting Windows execution state: {e}")
            return False

    def disable_sleep_prevention(self) -> bool:
        """Restores default OS execution state upon shutdown."""
        if not self._sleep_prevention_active:
            return True

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                logger.info("PowerManager: Windows sleep prevention deactivated (restored default).")
            except Exception as e:
                logger.debug(f"PowerManager: Error resetting execution state: {e}")

        self._sleep_prevention_active = False
        return True

    def register_wake_handler(self, handler: Callable[[float], None]) -> None:
        """Registers a callback invoked when a sleep-resume discontinuity is detected."""
        self._wake_handlers.append(handler)

    def record_tick(self, current_mono: Optional[float] = None) -> None:
        """Records a heartbeat tick."""
        self._last_tick = current_mono if current_mono is not None else time.monotonic()

    def check_discontinuity(
        self,
        expected_max_interval: float = 180.0,
        current_mono: Optional[float] = None
    ) -> Optional[float]:
        """
        Checks if elapsed monotonic time since last tick significantly exceeds expected interval.
        Returns gap duration in seconds if discontinuity detected, None otherwise.
        """
        now = current_mono if current_mono is not None else time.monotonic()
        gap = now - self._last_tick
        self._last_tick = now

        if gap > expected_max_interval:
            logger.warning(
                f"🚨 PowerManager: System sleep or clock discontinuity detected! "
                f"Gap: {gap:.1f}s (expected <= {expected_max_interval:.1f}s). "
                f"Flushing stale caches and resynchronizing schedules."
            )
            for handler in self._wake_handlers:
                try:
                    handler(gap)
                except Exception as e:
                    logger.error(f"Error in wake handler {handler}: {e}")
            return gap

        return None

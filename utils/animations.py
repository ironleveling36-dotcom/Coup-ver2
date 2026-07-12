"""
utils/animations.py - Lightweight inline animations for Telegram.

Telegram can't run real animations, but we create smooth motion by editing a
message through a few frames. Everything here is best-effort and NEVER raises:
if an edit fails (e.g. message unchanged / deleted) it is silently ignored, so
"every click stays error-free".
"""

import asyncio
import logging

from telegram.constants import ParseMode
from telegram.error import TelegramError

import config

logger = logging.getLogger(__name__)

# Spinner frames used for "working…" states.
SPINNER = ["◐", "◓", "◑", "◒"]
DOTS = ["", ".", "..", "..."]
CLOCKS = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]


def progress_bar(done: int, total: int, width: int = 12) -> str:
    """Return a text progress bar like ▰▰▰▱▱▱  50%."""
    total = max(int(total), 1)
    done = max(0, min(int(done), total))
    filled = int(width * done / total)
    pct = int(100 * done / total)
    return "▰" * filled + "▱" * (width - filled) + f"  {pct}%"


async def _safe_edit(msg, text, **kwargs):
    try:
        await msg.edit_text(text, **kwargs)
        return True
    except TelegramError:
        return False
    except Exception:
        return False


async def spin(msg, base_text: str, frames: int = 6, delay: float = 0.28):
    """
    Animate a spinner next to `base_text` on an existing message for a short
    while (used while a real async task runs concurrently in the background).
    """
    if not config.ANIMATIONS_ENABLED:
        return
    for i in range(frames):
        frame = SPINNER[i % len(SPINNER)]
        await _safe_edit(msg, f"{frame} {base_text}")
        await asyncio.sleep(delay)


async def run_with_spinner(msg, base_text: str, coro, min_frames: int = 3,
                           delay: float = 0.3):
    """
    Show a spinning loader on `msg` while awaiting `coro`. Returns the coro's
    result (or raises whatever the coro raised). Guarantees at least
    `min_frames` visible frames so the animation reads as intentional.
    """
    task = asyncio.ensure_future(coro)
    i = 0
    if config.ANIMATIONS_ENABLED:
        while not task.done() or i < min_frames:
            frame = SPINNER[i % len(SPINNER)]
            await _safe_edit(msg, f"{frame} {base_text}")
            await asyncio.sleep(delay)
            i += 1
            if i > 60:  # hard cap ~18s of animation
                break
    return await task


async def reveal(msg, lines: list[str], final_markup=None, delay: float = 0.35,
                 parse_mode=ParseMode.MARKDOWN):
    """
    Progressively reveal a multi-line message line-by-line for a "typing" feel,
    then attach the final keyboard on the last frame.
    """
    if not config.ANIMATIONS_ENABLED or len(lines) <= 1:
        await _safe_edit(msg, "\n".join(lines), reply_markup=final_markup,
                         parse_mode=parse_mode)
        return
    shown = []
    for idx, ln in enumerate(lines):
        shown.append(ln)
        is_last = idx == len(lines) - 1
        await _safe_edit(
            msg, "\n".join(shown),
            reply_markup=final_markup if is_last else None,
            parse_mode=parse_mode,
        )
        if not is_last:
            await asyncio.sleep(delay)


async def celebrate(msg, text: str, final_markup=None,
                    parse_mode=ParseMode.MARKDOWN):
    """A short celebratory flourish for successful actions."""
    if config.ANIMATIONS_ENABLED:
        for frame in ("✨", "🎉", "✅"):
            await _safe_edit(msg, f"{frame} {text.splitlines()[0]}")
            await asyncio.sleep(0.25)
    await _safe_edit(msg, text, reply_markup=final_markup, parse_mode=parse_mode)

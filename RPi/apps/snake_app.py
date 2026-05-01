"""
Snake — minimal one-button snake game for the OLED.

Controls:
    Forward button → turn right (the only available turn)
    Back button    → return to the home screen (default app_manager wiring)

The game loop runs on a daemon thread that ticks at TICK_S, advances the snake
one cell, eats food, grows, and either redraws or shows GAME OVER. Forward
clicks during play rotate the heading 90° clockwise. After game over, a
forward click starts a new game.
"""
import random
from threading import Thread, Event, RLock

from apps.base_app import BaseApp


_CELL = 4               # pixels per cell — 4px gives a chunky, readable snake on 128x64
_TICK_S = 0.18          # game step interval; smaller = faster snake


class SnakeApp(BaseApp):
    name = "snake"
    label = "Snake"

    # Headings as (dx, dy). Order matters: turning right = next index mod 4.
    _HEADINGS = [(1, 0), (0, 1), (-1, 0), (0, -1)]

    def __init__(self, shared_class):
        super().__init__(shared_class)
        # Reentrant: render_display re-acquires the lock from the same thread
        # that originally took it via on_click / the game loop, so a plain Lock
        # would deadlock the moment update_display fans out to render_display.
        self._lock = RLock()
        self._stop = Event()
        self._thread = None
        # Game state — initialized lazily in _reset_game once the display is known.
        self._cols = 0
        self._rows = 0
        self._origin_x = 0
        self._origin_y = 0
        self._snake = []
        self._heading_idx = 0
        self._pending_turns = 0
        self._food = (0, 0)
        self._score = 0
        self._game_over = False

    # ── Mount / unmount ────────────────────────────────────────────────────────

    def on_mount(self):
        self.shared_class.camera_streaming = False
        self._reset_game()
        self._stop.clear()
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.shared_class.display.update_display({"app": self.name})

    def on_unmount(self):
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=0.5)

    # ── Input ──────────────────────────────────────────────────────────────────

    def on_click(self, click_count):
        if click_count <= 0:
            return
        needs_redraw = False
        with self._lock:
            if self._game_over:
                self._reset_game()
                needs_redraw = True
            else:
                # Buffer turns so a forward press always rotates exactly one tick,
                # even if the user double-taps faster than _TICK_S.
                self._pending_turns += 1
        if needs_redraw:
            # Outside the lock: update_display fans out to render_display,
            # which re-acquires self._lock. Even with RLock, holding the
            # game lock across an SPI write blocks the tick thread for no
            # benefit, so we let it go first.
            self.shared_class.display.update_display({"app": self.name})

    # ── Game loop ──────────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop.wait(_TICK_S):
            with self._lock:
                if self._game_over:
                    continue
                self._step()
            try:
                self.shared_class.display.update_display({"app": self.name})
            except Exception:
                pass

    def _step(self):
        if self._pending_turns > 0:
            self._heading_idx = (self._heading_idx + self._pending_turns) % 4
            self._pending_turns = 0

        dx, dy = self._HEADINGS[self._heading_idx]
        head_x, head_y = self._snake[0]
        new_head = (head_x + dx, head_y + dy)

        # Wall collision.
        nx, ny = new_head
        if nx < 0 or ny < 0 or nx >= self._cols or ny >= self._rows:
            self._game_over = True
            return

        ate = (new_head == self._food)
        # Self collision: compare against body, but exclude the tail when we're
        # not eating because the tail will move out of the way this tick.
        body_to_check = self._snake if ate else self._snake[:-1]
        if new_head in body_to_check:
            self._game_over = True
            return

        self._snake.insert(0, new_head)
        if ate:
            self._score += 1
            self._food = self._spawn_food()
        else:
            self._snake.pop()

    # ── Setup / random helpers ─────────────────────────────────────────────────

    def _reset_game(self):
        display = self.shared_class.display
        oled_w = display.oled.width
        oled_h = display.oled.height
        content_y = display.HEADER_CONTENT_START_Y

        cols = oled_w // _CELL
        rows = (oled_h - content_y) // _CELL
        self._cols = max(8, cols)
        self._rows = max(4, rows)

        # Center the playfield so any leftover px sit as an even border.
        self._origin_x = (oled_w - self._cols * _CELL) // 2
        self._origin_y = content_y + (oled_h - content_y - self._rows * _CELL) // 2

        cx = self._cols // 2
        cy = self._rows // 2
        # Length-3 snake heading right.
        self._snake = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]
        self._heading_idx = 0
        self._pending_turns = 0
        self._score = 0
        self._game_over = False
        self._food = self._spawn_food()

    def _spawn_food(self):
        occupied = set(self._snake)
        free = [
            (x, y)
            for x in range(self._cols)
            for y in range(self._rows)
            if (x, y) not in occupied
        ]
        if not free:
            # Snake fills the board — treat the next tick as a (well-earned) game over.
            self._game_over = True
            return (-1, -1)
        return random.choice(free)

    # ── Rendering ──────────────────────────────────────────────────────────────

    def render_display(self, display):
        if not display.hardware_available:
            return
        with self._lock:
            snake = list(self._snake)
            food = self._food
            score = self._score
            game_over = self._game_over
            cols = self._cols
            rows = self._rows
            ox = self._origin_x
            oy = self._origin_y

        oled = display.oled
        oled.fill(0)
        display.draw_app_header(f"Snake  {score}")

        # Playfield border (1 px outline around the cell grid).
        bw = cols * _CELL
        bh = rows * _CELL
        oled.rect(ox - 1, oy - 1, bw + 2, bh + 2, 1)

        if food[0] >= 0:
            fx = ox + food[0] * _CELL
            fy = oy + food[1] * _CELL
            oled.fill_rect(fx + 1, fy + 1, _CELL - 2, _CELL - 2, 1)

        for i, (sx, sy) in enumerate(snake):
            px = ox + sx * _CELL
            py = oy + sy * _CELL
            if i == 0:
                oled.fill_rect(px, py, _CELL, _CELL, 1)
            else:
                oled.fill_rect(px + 1, py + 1, _CELL - 2, _CELL - 2, 1)

        if game_over:
            self._draw_game_over_overlay(oled, ox, oy, bw, bh, score)

        oled.show()

    def _draw_game_over_overlay(self, oled, ox, oy, bw, bh, score):
        msg1 = "GAME OVER"
        msg2 = f"Score {score}"
        msg3 = "Click to restart"

        # Black panel with a 1-px white outline, centered in the playfield.
        pad_x = 4
        pad_y = 3
        text_h = 8
        line_gap = 2
        panel_h = pad_y * 2 + text_h * 3 + line_gap * 2
        panel_w = pad_x * 2 + max(len(msg1), len(msg2), len(msg3)) * 6
        panel_w = min(panel_w, bw)
        px = ox + (bw - panel_w) // 2
        py = oy + (bh - panel_h) // 2

        oled.fill_rect(px, py, panel_w, panel_h, 0)
        oled.rect(px, py, panel_w, panel_h, 1)

        def _line(msg, row):
            tx = px + (panel_w - len(msg) * 6) // 2
            ty = py + pad_y + row * (text_h + line_gap)
            oled.text(msg, tx, ty, 1)

        _line(msg1, 0)
        _line(msg2, 1)
        _line(msg3, 2)

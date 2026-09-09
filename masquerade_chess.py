#!/usr/bin/env python3
"""
MASQUERADE CHESS
================

Every piece on the board is lying about what it is.

The board is set up normally, and every piece keeps the shape it was born with:
the thing on e1 still *looks* like a king, the things on rank 2 still *look*
like pawns. But at the start of each game both armies secretly shuffle their
16 true identities among their 16 bodies. That innocent a2 pawn might move like
a rook. The knight on g1 might be the queen. The one thing you can count on is
that the king is hiding somewhere on the back rank — a pawn shape is never the
king.

You don't get told any of it. You find out by playing.

RULES THAT DIFFER FROM CHESS
----------------------------
* Win by CAPTURING the enemy king, not by checkmate. You may leave your own
  king hanging; nothing stops you. (You'll be told when it's attacked.)
* Trying an illegal move is a "probe". You learn that the piece cannot do that,
  which is real information. You get a few free probes per game; after that a
  failed probe costs you your turn.
* Both sides deduce each other the same way. The engine does not know which of
  your pieces is your king either; it works from the same public evidence you
  see in the side panel.
* The king is always dealt into one of the eight back-rank bodies, never into a
  pawn shape. Both sides know this, so pawn shapes start with the king already
  ruled out.
* A true pawn may double-step on its first move from wherever it starts, and
  promotes on the far rank (and visibly turns into a queen when it does).
* No castling, no en passant. Neither makes sense when identities are hidden.

RUNNING IT
----------
    python masquerade_chess.py

You pick an opponent when the game starts: the bot, or a second person on the
same computer. In 1v1 the secrets follow whoever's turn it is, so the player
who isn't moving should look away.

Needs only the standard library. On Debian/Ubuntu you may need tkinter:
    sudo apt install python3-tk

Everything worth fiddling with is in the SETTINGS block below.
"""

import random
import sys

try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox
except ImportError:  # headless machines, or python3-tk not installed
    tk = None


# --------------------------------------------------------------------------
# SETTINGS
# --------------------------------------------------------------------------

PROBE_MODE = "limited"      # "limited" | "free" | "forfeit"
FREE_PROBES = 3             # only used when PROBE_MODE == "limited"
KNOW_OWN_KING = True       # you're shown which of your pieces is really the king
REVEAL_OWN_AFTER_MOVE = True  # your own pieces confess to you once they've moved
DEDUCTION_ASSIST = True     # side panel narrows down candidate identities for you
AI_DELAY_MS = 500
SQ = 74                     # square size in pixels
SEED = None                 # set an int to replay the same shuffle


# --------------------------------------------------------------------------
# RULES ENGINE  (no tkinter below this line until the App class)
# --------------------------------------------------------------------------

ROLES = ("pawn", "knight", "bishop", "rook", "queen", "king")
ARMY = ["pawn"] * 8 + ["knight"] * 2 + ["bishop"] * 2 + ["rook"] * 2 + ["queen", "king"]
BACK_MODELS = ["rook", "knight", "bishop", "queen", "king", "bishop", "knight", "rook"]

GLYPH = {"pawn": "\u265F", "knight": "\u265E", "bishop": "\u265D",
         "rook": "\u265C", "queen": "\u265B", "king": "\u265A"}
INITIAL = {"pawn": "P", "knight": "N", "bishop": "B",
           "rook": "R", "queen": "Q", "king": "K"}
NAME = {r: r.capitalize() for r in ROLES}

# What a piece is worth when you know what it is.
OWN_VALUE = {"pawn": 1, "knight": 3, "bishop": 3.2, "rook": 5, "queen": 9, "king": 100}
# What an *unidentified* enemy piece is worth in expectation. The king is only
# mildly inflated here: a body that might be the king is a tempting target, but
# not worth throwing a queen at on a 1-in-6 chance.
GUESS_VALUE = {"pawn": 1, "knight": 3, "bishop": 3.2, "rook": 5, "queen": 9, "king": 14}

KNIGHT_HOPS = [(-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)]
ORTHO = [(-1, 0), (1, 0), (0, -1), (0, 1)]
DIAG = [(-1, -1), (-1, 1), (1, -1), (1, 1)]


def initial_candidates(model):
    """The king is never dealt into a pawn shape, and everyone knows it."""
    return set(ROLES) - {"king"} if model == "pawn" else set(ROLES)


def square_name(rc):
    r, c = rc
    return "abcdefgh"[c] + str(8 - r)


class Piece:
    __slots__ = ("color", "model", "role", "pid", "moved", "candidates", "probes_failed")

    def __init__(self, color, model, role, pid):
        self.color = color          # "w" or "b"
        self.model = model          # what everyone SEES
        self.role = role            # what it actually IS
        self.pid = pid
        self.moved = False
        self.candidates = initial_candidates(model)  # public, shared by both players
        self.probes_failed = 0


def targets(board, r, c, color, role, moved):
    """Every square a piece of `role` standing on (r, c) could move to."""
    out = set()

    if role == "pawn":
        d = -1 if color == "w" else 1
        r1 = r + d
        if 0 <= r1 < 8 and board[r1][c] is None:
            out.add((r1, c))
            r2 = r + 2 * d
            if not moved and 0 <= r2 < 8 and board[r2][c] is None:
                out.add((r2, c))
        for dc in (-1, 1):
            rr, cc = r + d, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8:
                t = board[rr][cc]
                if t is not None and t.color != color:
                    out.add((rr, cc))

    elif role == "knight":
        for dr, dc in KNIGHT_HOPS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8:
                t = board[rr][cc]
                if t is None or t.color != color:
                    out.add((rr, cc))

    elif role == "king":
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr or dc:
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < 8 and 0 <= cc < 8:
                        t = board[rr][cc]
                        if t is None or t.color != color:
                            out.add((rr, cc))

    else:
        dirs = ORTHO + DIAG if role == "queen" else (ORTHO if role == "rook" else DIAG)
        for dr, dc in dirs:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                t = board[rr][cc]
                if t is None:
                    out.add((rr, cc))
                else:
                    if t.color != color:
                        out.add((rr, cc))
                    break
                rr, cc = rr + dr, cc + dc

    return out


class Game:
    """Board state and rules. Knows nothing about the interface."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.reset()

    # -- setup -------------------------------------------------------------

    def reset(self):
        self.board = [[None] * 8 for _ in range(8)]
        self.turn = "w"
        self.over = None            # None, or a result string
        self.winner = None
        self.history = []           # human-readable move log
        self.probes_left = {"w": FREE_PROBES, "b": FREE_PROBES}
        self.last_move = None       # (from, to)
        pid = 0
        for color, back_r, pawn_r in (("b", 0, 1), ("w", 7, 6)):
            roles = ARMY[:]
            self.rng.shuffle(roles)
            back, front = roles[:8], roles[8:]
            if "king" in front:
                i, j = front.index("king"), self.rng.randrange(8)
                front[i], back[j] = back[j], front[i]
            for c in range(8):
                self.board[back_r][c] = Piece(color, BACK_MODELS[c], back[c], pid)
                pid += 1
                self.board[pawn_r][c] = Piece(color, "pawn", front[c], pid)
                pid += 1

    # -- queries -----------------------------------------------------------

    def at(self, rc):
        return self.board[rc[0]][rc[1]]

    def pieces(self, color):
        for r in range(8):
            for c in range(8):
                p = self.board[r][c]
                if p is not None and p.color == color:
                    yield (r, c), p

    def legal_targets(self, rc):
        p = self.at(rc)
        if p is None:
            return set()
        return targets(self.board, rc[0], rc[1], p.color, p.role, p.moved)

    def all_moves(self, color):
        out = []
        for rc, p in self.pieces(color):
            for t in targets(self.board, rc[0], rc[1], p.color, p.role, p.moved):
                out.append((rc, t))
        return out

    def find_king(self, color):
        for rc, p in self.pieces(color):
            if p.role == "king":
                return rc
        return None

    def king_attacked(self, color):
        k = self.find_king(color)
        if k is None:
            return False
        enemy = "b" if color == "w" else "w"
        for rc, p in self.pieces(enemy):
            if k in targets(self.board, rc[0], rc[1], p.color, p.role, p.moved):
                return True
        return False

    # -- public deduction --------------------------------------------------

    def _observe_success(self, p, frm, to):
        """Everyone just watched this move happen. Rule out roles that couldn't."""
        p.candidates = {
            role for role in p.candidates
            if to in targets(self.board, frm[0], frm[1], p.color, role, p.moved)
        } or set(p.candidates)

    def _observe_failure(self, p, frm, to):
        """Everyone just watched this move get refused. Rule out roles that could."""
        p.probes_failed += 1
        remaining = {
            role for role in p.candidates
            if to not in targets(self.board, frm[0], frm[1], p.color, role, p.moved)
        }
        if remaining:
            p.candidates = remaining

    # -- moving ------------------------------------------------------------

    def attempt(self, frm, to):
        """
        Try to move. Returns a dict:
            ok        - was it a legal move
            text      - one line for the status bar
            lost_turn - did a failed probe cost the turn
        """
        if self.over:
            return {"ok": False, "text": "The game is finished.", "lost_turn": False}
        p = self.at(frm)
        if p is None or p.color != self.turn or frm == to:
            return {"ok": False, "text": "", "lost_turn": False}

        if to in targets(self.board, frm[0], frm[1], p.color, p.role, p.moved):
            self._observe_success(p, frm, to)
            text = self._apply(frm, to)
            return {"ok": True, "text": text, "lost_turn": False}

        # Illegal: a probe.
        self._observe_failure(p, frm, to)
        note = "{} cannot reach {}.".format(GLYPH[p.model], square_name(to))
        self.history.append("  ?  {}{}\u2717{}".format(
            "W" if p.color == "w" else "B", square_name(frm), square_name(to)))

        if PROBE_MODE == "free":
            return {"ok": False, "text": note + " (free probe)", "lost_turn": False}
        if PROBE_MODE == "limited" and self.probes_left[p.color] > 0:
            self.probes_left[p.color] -= 1
            left = self.probes_left[p.color]
            return {"ok": False,
                    "text": "{} {} free probe{} left.".format(note, left, "" if left == 1 else "s"),
                    "lost_turn": False}

        self._pass_turn()
        return {"ok": False, "text": note + " You lose the turn.", "lost_turn": True}

    def _apply(self, frm, to):
        p = self.at(frm)
        captured = self.at(to)
        self.board[to[0]][to[1]] = p
        self.board[frm[0]][frm[1]] = None
        p.moved = True

        line = "{}{}{}{}{}".format(
            "W " if p.color == "w" else "B ", GLYPH[p.model],
            square_name(frm), "x" if captured else "\u2013", square_name(to))

        promoted = False
        if p.role == "pawn" and to[0] == (0 if p.color == "w" else 7):
            p.role = "queen"
            p.model = "queen"
            p.candidates = {"queen"}
            promoted = True
            line += "=Q"

        self.last_move = (frm, to)
        self.history.append(line)

        if captured is not None and captured.role == "king":
            self.winner = p.color
            self.over = "{} captured the {} king.".format(
                "White" if p.color == "w" else "Black",
                "black" if captured.color == "b" else "white")
            return "The {} was the king! {}".format(
                NAME[captured.model], self.over)

        self._pass_turn()

        msg = ""
        if captured is not None:
            msg = "Captured a {}-shaped piece \u2014 it was really a {}. ".format(
                NAME[captured.model].lower(), NAME[captured.role].lower())
        if promoted:
            msg += "A hidden pawn promoted to a queen. "
        return msg

    def _pass_turn(self):
        self.turn = "b" if self.turn == "w" else "w"
        if not self.all_moves(self.turn):
            self.over = "No legal moves left \u2014 a draw."
            self.winner = None

    # -- engine ------------------------------------------------------------

    def guess_value(self, p):
        """What the *other* side thinks an enemy body is worth."""
        if not p.candidates:
            return 3.0
        if p.candidates == {"king"}:
            return 500.0
        return sum(GUESS_VALUE[r] for r in p.candidates) / len(p.candidates)

    def threat_level(self, board, sq, by_color):
        """
        Probability-ish estimate that `by_color` can hit `sq` next move, judged
        only from what the candidates allow. Deliberately not omniscient.
        """
        worst = 0.0
        for r in range(8):
            for c in range(8):
                p = board[r][c]
                if p is None or p.color != by_color:
                    continue
                if not p.candidates:
                    continue
                hits = sum(1 for role in p.candidates
                           if sq in targets(board, r, c, p.color, role, p.moved))
                worst = max(worst, hits / len(p.candidates))
                if worst >= 1.0:
                    return 1.0
        return worst

    def engine_move(self, color="b"):
        moves = self.all_moves(color)
        if not moves:
            return None
        enemy = "w" if color == "b" else "b"
        best, best_score = [], None

        for frm, to in moves:
            mover = self.at(frm)
            victim = self.at(to)
            score = 0.0

            if victim is not None:
                score += 9.0 * self.guess_value(victim)

            # Nudge pieces forward and toward the middle, gently.
            forward = (to[0] - frm[0]) if color == "b" else (frm[0] - to[0])
            score += 0.10 * forward
            score += 0.12 * (3.5 - abs(3.5 - to[1]))

            # How exposed is the destination? Uses only public evidence.
            self.board[to[0]][to[1]] = mover
            self.board[frm[0]][frm[1]] = None
            risk = self.threat_level(self.board, to, enemy)
            self.board[frm[0]][frm[1]] = mover
            self.board[to[0]][to[1]] = victim
            score -= 6.5 * risk * OWN_VALUE[mover.role]

            score += self.rng.uniform(0.0, 0.7)

            if best_score is None or score > best_score + 1e-9:
                best, best_score = [(frm, to)], score
            elif abs(score - best_score) <= 1e-9:
                best.append((frm, to))

        frm, to = self.rng.choice(best)
        p = self.at(frm)
        self._observe_success(p, frm, to)
        return frm, to, self._apply(frm, to)


# --------------------------------------------------------------------------
# INTERFACE
# --------------------------------------------------------------------------

BG = "#1d1b19"
PANEL = "#26231f"
INK = "#ece5d8"
MUTED = "#948b7c"
LIGHT_SQ = "#d8cfb8"
DARK_SQ = "#6f7f6a"
SEL_SQ = "#d9a441"
LAST_SQ = "#a8a05f"
CHECK_SQ = "#b5533f"
WHITE_PIECE = "#fbf7ee"
BLACK_PIECE = "#221f1c"
COORD_ON_LIGHT = "#a2977c"   # tkinter has no alpha channel, so these are
COORD_ON_DARK = "#8ea089"    # pre-mixed against the two square colours
HINT_DOT = "#4c4a43"


def pick_font(root):
    families = set(tkfont.families(root))
    for candidate in ("Segoe UI Symbol", "DejaVu Sans", "FreeSerif", "Symbola",
                      "Noto Sans Symbols 2", "Apple Symbols", "Arial Unicode MS"):
        if candidate in families:
            return candidate
    return "TkDefaultFont"


class App:
    def __init__(self, root, mode="bot"):
        self.root = root
        self.mode = mode            # "bot" | "hotseat"
        self.game = Game(SEED)
        self.sel = None
        self.busy = False
        self.revealed = False

        root.title("Masquerade Chess")
        root.configure(bg=BG)
        root.resizable(False, False)

        self.piece_font_name = pick_font(root)
        self.piece_font = tkfont.Font(family=self.piece_font_name, size=int(SQ * 0.62))
        self.badge_font = tkfont.Font(family="TkDefaultFont", size=9, weight="bold")
        self.coord_font = tkfont.Font(family="TkDefaultFont", size=8)
        self.log_font = tkfont.nametofont("TkFixedFont").copy()
        self.log_font.configure(size=9)

        wrap = tk.Frame(root, bg=BG, padx=14, pady=14)
        wrap.pack()

        self.canvas = tk.Canvas(wrap, width=SQ * 8, height=SQ * 8,
                                highlightthickness=0, bg=BG, cursor="hand2")
        self.canvas.grid(row=0, column=0)
        self.canvas.bind("<Button-1>", self.on_click)

        self.build_panel(wrap)
        self.redraw()
        self.set_status("White moves. Nobody knows what anything is yet.")

    @property
    def viewer(self):
        """Whose secrets the board is currently allowed to show."""
        return self.game.turn if self.mode == "hotseat" else "w"

    # -- panel -------------------------------------------------------------

    def build_panel(self, wrap):
        side = tk.Frame(wrap, bg=PANEL, padx=16, pady=14, width=320)
        side.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        side.grid_propagate(False)

        self.turn_label = tk.Label(side, text="", bg=PANEL, fg=INK,
                                   font=("TkDefaultFont", 14, "bold"), anchor="w")
        self.turn_label.pack(fill="x")

        self.status = tk.Label(side, text="", bg=PANEL, fg=MUTED, anchor="w",
                               justify="left", wraplength=280,
                               font=("TkDefaultFont", 10))
        self.status.pack(fill="x", pady=(4, 12))

        tk.Frame(side, bg="#3a352e", height=1).pack(fill="x")

        self.inspect = tk.Label(side, text="Click one of your pieces.",
                                bg=PANEL, fg=INK, anchor="nw", justify="left",
                                wraplength=280, font=("TkDefaultFont", 10),
                                height=8)
        self.inspect.pack(fill="x", pady=(10, 10))

        tk.Frame(side, bg="#3a352e", height=1).pack(fill="x")

        log_box = tk.Frame(side, bg=PANEL)
        log_box.pack(fill="both", expand=True, pady=(10, 10))
        self.log = tk.Text(log_box, bg="#1b1916", fg=MUTED, height=11, width=1,
                           relief="flat", font=self.log_font, padx=8, pady=6,
                           state="disabled", wrap="none")
        self.log.pack(side="left", fill="both", expand=True)
        bar = tk.Scrollbar(log_box, command=self.log.yview, width=10)
        bar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=bar.set)

        buttons = tk.Frame(side, bg=PANEL)
        buttons.pack(fill="x")
        made = {}
        for key, text, cmd in (("new", "New game", self.new_game),
                               ("mode", "", self.switch_mode),
                               ("rules", "How to play", self.show_rules),
                               ("reveal", "Give up and reveal", self.reveal_all)):
            made[key] = tk.Button(buttons, text=text, command=cmd, relief="flat",
                                  bg="#3a352e", fg=INK, activebackground="#4a443a",
                                  activeforeground=INK, font=("TkDefaultFont", 10),
                                  pady=5)
            made[key].pack(fill="x", pady=2)
        self.mode_button = made["mode"]
        self.refresh_mode_button()

    def refresh_mode_button(self):
        self.mode_button.configure(
            text="Switch to bot" if self.mode == "hotseat" else "Switch to 1v1")
        self.root.title("Masquerade Chess — "
                        + ("1v1" if self.mode == "hotseat" else "vs bot"))

    def set_status(self, text):
        self.status.configure(text=text)

    def refresh_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", "\n".join(self.game.history[-200:]))
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_turn(self):
        g = self.game
        if g.over:
            self.turn_label.configure(text="Game over", fg="#d9a441")
            return
        if self.mode == "hotseat":
            who = "White to move" if g.turn == "w" else "Black to move"
        else:
            who = "Your move" if g.turn == "w" else "Black is thinking"
        extra = ""
        if PROBE_MODE == "limited" and (self.mode == "hotseat" or g.turn == "w"):
            left = g.probes_left[g.turn]
            extra = "   \u00b7   {} free probe{}".format(left, "" if left == 1 else "s")
        self.turn_label.configure(text=who + extra, fg=INK)

    def describe(self, rc):
        p = self.game.at(rc)
        if p is None:
            self.inspect.configure(text="Click one of your pieces.")
            return
        if p.color == self.viewer:
            side = "Your"
        else:
            side = "White's" if p.color == "w" else "Black's"
        lines = ["{} {}-shaped piece on {}".format(side, NAME[p.model].lower(), square_name(rc))]

        known = None
        if p.color == self.viewer and KNOW_OWN_KING and p.role == "king":
            known = "king"
        if p.color == self.viewer and REVEAL_OWN_AFTER_MOVE and p.moved:
            known = p.role

        if known:
            lines.append("Truly a {}.".format(NAME[known].upper()))
        elif DEDUCTION_ASSIST:
            maybe = [NAME[r] for r in ROLES if r in p.candidates]
            ruled = [NAME[r] for r in ROLES if r not in p.candidates]
            lines.append("Could be: " + ", ".join(maybe))
            if ruled:
                lines.append("Ruled out: " + ", ".join(ruled))
        else:
            lines.append("Unknown.")

        lines.append("")
        lines.append("Moves made: {}    Failed probes: {}".format(
            "some" if p.moved else "0", p.probes_failed))
        self.inspect.configure(text="\n".join(lines))

    # -- drawing -----------------------------------------------------------

    def redraw(self):
        c = self.canvas
        c.delete("all")
        g = self.game
        check_sq = None
        if KNOW_OWN_KING and not g.over and g.king_attacked(self.viewer):
            check_sq = g.find_king(self.viewer)

        for r in range(8):
            for cc in range(8):
                x, y = cc * SQ, r * SQ
                fill = LIGHT_SQ if (r + cc) % 2 == 0 else DARK_SQ
                if g.last_move and (r, cc) in g.last_move:
                    fill = LAST_SQ
                if check_sq == (r, cc):
                    fill = CHECK_SQ
                if self.sel == (r, cc):
                    fill = SEL_SQ
                c.create_rectangle(x, y, x + SQ, y + SQ, fill=fill, outline="")

                coord_ink = COORD_ON_LIGHT if (r + cc) % 2 == 0 else COORD_ON_DARK
                if cc == 0:
                    c.create_text(x + 5, y + 5, text=str(8 - r), anchor="nw",
                                  fill=coord_ink, font=self.coord_font)
                if r == 7:
                    c.create_text(x + SQ - 5, y + SQ - 4, text="abcdefgh"[cc],
                                  anchor="se", fill=coord_ink, font=self.coord_font)

        if self.sel is not None:
            for t in self.hint_squares():
                x, y = t[1] * SQ + SQ // 2, t[0] * SQ + SQ // 2
                c.create_oval(x - 7, y - 7, x + 7, y + 7, fill=HINT_DOT,
                              stipple="gray50", outline="")

        for r in range(8):
            for cc in range(8):
                p = g.board[r][cc]
                if p is None:
                    continue
                x, y = cc * SQ + SQ // 2, r * SQ + SQ // 2 - 2
                glyph = GLYPH[p.model]
                color = WHITE_PIECE if p.color == "w" else BLACK_PIECE
                if p.color == "w":
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        c.create_text(x + dx, y + dy, text=glyph, fill="#2a2622",
                                      font=self.piece_font)
                c.create_text(x, y, text=glyph, fill=color, font=self.piece_font)

                badge = None
                if self.revealed or g.over:
                    badge = INITIAL[p.role]
                elif p.color == self.viewer and KNOW_OWN_KING and p.role == "king":
                    badge = "K"
                elif p.color == self.viewer and REVEAL_OWN_AFTER_MOVE and p.moved:
                    badge = INITIAL[p.role]
                if badge:
                    bx, by = cc * SQ + SQ - 13, r * SQ + 13
                    c.create_oval(bx - 10, by - 10, bx + 10, by + 10,
                                  fill="#d9a441", outline="#2a2622")
                    c.create_text(bx, by, text=badge, fill="#2a2622",
                                  font=self.badge_font)

        self.refresh_turn()
        self.refresh_log()

    def hint_squares(self):
        """Only ever shows moves for pieces you have already identified."""
        p = self.game.at(self.sel)
        if p is None or p.color != self.viewer:
            return set()
        identified = (self.revealed
                      or (KNOW_OWN_KING and p.role == "king")
                      or (REVEAL_OWN_AFTER_MOVE and p.moved))
        if not identified:
            return set()
        return self.game.legal_targets(self.sel)

    # -- interaction -------------------------------------------------------

    def on_click(self, event):
        if self.busy or self.game.over:
            return
        r, c = event.y // SQ, event.x // SQ
        if not (0 <= r < 8 and 0 <= c < 8):
            return
        rc = (r, c)
        p = self.game.at(rc)

        if self.sel == rc:
            self.sel = None
            self.redraw()
            return

        if p is not None and p.color == self.game.turn:
            self.sel = rc
            self.describe(rc)
            self.redraw()
            return

        if self.sel is None:
            if p is not None:
                self.describe(rc)
            else:
                self.set_status("Pick one of your own pieces first.")
            self.redraw()
            return

        result = self.game.attempt(self.sel, rc)
        moved_from = self.sel
        turn_changed = result["ok"] or result["lost_turn"]
        if turn_changed:
            self.sel = None
        text = result["text"] or ""
        if turn_changed and self.mode == "hotseat" and not self.game.over:
            text += " {} to move — the other player should look away.".format(
                "White" if self.game.turn == "w" else "Black")
            if KNOW_OWN_KING and self.game.king_attacked(self.game.turn):
                text += " Your king is under attack."
        self.set_status(text)
        if not result["ok"] and not result["lost_turn"]:
            self.describe(moved_from)
        elif self.sel is None and not self.game.over:
            self.inspect.configure(text="Click one of your pieces.")
        self.redraw()

        if self.game.over:
            self.finish()
        elif self.mode == "bot" and self.game.turn == "b":
            self.busy = True
            self.root.after(AI_DELAY_MS, self.engine_turn)

    def engine_turn(self):
        move = self.game.engine_move("b")
        self.busy = False
        if move is None:
            self.redraw()
            self.finish()
            return
        frm, to, note = move
        tail = ""
        if not self.game.over and KNOW_OWN_KING and self.game.king_attacked("w"):
            tail = "Your king is under attack."
        self.set_status("Black plays {}\u2013{}. {}{}".format(
            square_name(frm), square_name(to), note, tail))
        self.redraw()
        if self.game.over:
            self.finish()

    def finish(self):
        self.revealed = True
        self.redraw()
        g = self.game
        if g.winner is None:
            title, msg = "Draw", g.over
        elif self.mode == "hotseat":
            title = "White wins" if g.winner == "w" else "Black wins"
            msg = g.over
        elif g.winner == "w":
            title, msg = "You win", "You found and took the black king.\n\n" + g.over
        else:
            title, msg = "You lose", g.over
        messagebox.showinfo(title, msg + "\n\nEvery identity is now shown on the board.")

    def new_game(self):
        self.game = Game()
        self.sel = None
        self.busy = False
        self.revealed = False
        self.inspect.configure(text="Click one of your pieces.")
        self.set_status("New shuffle. Both armies are strangers again.")
        self.redraw()

    def switch_mode(self):
        self.mode = "bot" if self.mode == "hotseat" else "hotseat"
        self.refresh_mode_button()
        self.new_game()
        self.set_status("Now playing {}. New shuffle, both armies are strangers again."
                        .format("1v1 on this computer" if self.mode == "hotseat"
                                else "against the bot"))

    def reveal_all(self):
        self.revealed = not self.revealed
        self.set_status("All identities shown." if self.revealed
                        else "Identities hidden again.")
        self.redraw()

    def show_rules(self):
        opponent = ("• 1v1: the board only shows the secrets of whoever is to "
                    "move, so the other player should look away between turns.\n"
                    if self.mode == "hotseat" else
                    "• Black is just as blind as you are: it deduces your pieces "
                    "from the same public evidence.\n")
        messagebox.showinfo("How to play", (
            "The board looks like chess, but each army has secretly shuffled its "
            "16 true identities among its 16 bodies. A pawn shape may move like a "
            "rook. Your king is hiding inside one of your back-rank shapes.\n\n"
            "\u2022 Win by capturing the enemy king. There is no checkmate.\n"
            "\u2022 The king is always dealt into one of the eight back-rank bodies, "
            "never a pawn shape \u2014 so there are only eight suspects.\n"
            "\u2022 Click a piece, then click a square. If that piece can't do it, "
            "you've learned something \u2014 the side panel keeps track.\n"
            "\u2022 Failed attempts are free for your first {} probes, then they "
            "cost you the turn.\n"
            "\u2022 Your own king is marked. Your other pieces confess to you once "
            "they've successfully moved.\n"
            + opponent +
            "\u2022 No castling, no en passant. Hidden pawns still double-step on "
            "their first move and still promote."
        ).format(FREE_PROBES))


def ask_mode(root):
    """Modal opponent picker. Returns "bot", "hotseat", or None if closed."""
    choice = {"value": None}
    win = tk.Toplevel(root)
    win.title("Masquerade Chess")
    win.configure(bg=BG, padx=26, pady=22)
    win.resizable(False, False)

    tk.Label(win, text="Masquerade Chess", bg=BG, fg=INK,
             font=("TkDefaultFont", 16, "bold")).pack()
    tk.Label(win, text="Who is playing black?", bg=BG, fg=MUTED,
             font=("TkDefaultFont", 10)).pack(pady=(4, 14))

    def pick(value):
        choice["value"] = value
        win.destroy()

    for text, value in (("Play against the bot", "bot"),
                        ("1v1 on this computer", "hotseat")):
        tk.Button(win, text=text, command=lambda v=value: pick(v), relief="flat",
                  bg="#3a352e", fg=INK, activebackground="#4a443a",
                  activeforeground=INK, font=("TkDefaultFont", 11),
                  width=24, pady=7).pack(pady=3)

    win.transient(root)
    win.grab_set()
    root.wait_window(win)
    return choice["value"]


def main():
    if tk is None:
        print("This game needs tkinter, which isn't available in this Python.\n"
              "  Debian/Ubuntu:  sudo apt install python3-tk\n"
              "  Fedora:         sudo dnf install python3-tkinter\n"
              "  macOS/Windows:  install Python from python.org, which bundles it.",
              file=sys.stderr)
        return 1
    root = tk.Tk()
    root.withdraw()
    mode = ask_mode(root)
    if mode is None:
        root.destroy()
        return 0
    root.deiconify()
    App(root, mode)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
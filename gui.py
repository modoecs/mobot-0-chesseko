"""Point-and-click GUI for playing against the engine.

    python gui.py

Uses Tkinter, which ships with the standard Python installer. The engine
thinks in a background thread so the window stays responsive.
"""

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from board import (BISHOP, BLACK, KING, KNIGHT, PAWN, QUEEN, ROOK, WHITE, Board,
                   move_from, move_promo, move_to, square)
from play import format_eval
from search import Searcher

SQ = 72  # pixels per squared
LIGHT, DARK = '#f0d9b5', '#b58863'
LAST_LIGHT, LAST_DARK = '#cdd26a', '#aaa23a'
SELECTED = '#829769'
CHECK = '#e8655a'
TARGET = '#5f7a45'

SOLID = {PAWN: '♟', KNIGHT: '♞', BISHOP: '♝',
         ROOK: '♜', QUEEN: '♛', KING: '♚'}
OUTLINE = {PAWN: '♙', KNIGHT: '♘', BISHOP: '♗',
           ROOK: '♖', QUEEN: '♕', KING: '♔'}


def pick_font_family(root):
    families = set(tkfont.families(root))
    for name in ('Segoe UI Symbol', 'DejaVu Sans', 'Arial Unicode MS', 'FreeSerif'):
        if name in families:
            return name
    return 'TkDefaultFont'


class ChessApp:
    def __init__(self, root):
        self.root = root
        root.title('mobot-0 chesseko')
        root.resizable(False, False)
        family = pick_font_family(root)
        self.piece_font = (family, int(SQ * 0.62))
        self.coord_font = ('Segoe UI', 9, 'bold')

        self.searcher = Searcher()
        self.results = queue.Queue()
        self.worker = None
        self.game_id = 0
        self.thinking = False

        self.canvas = tk.Canvas(root, width=8 * SQ, height=8 * SQ, highlightthickness=0)
        self.canvas.grid(row=0, column=0, padx=12, pady=12)
        self.canvas.bind('<Button-1>', self.on_click)

        panel = tk.Frame(root)
        panel.grid(row=0, column=1, sticky='ns', padx=(0, 12), pady=12)
        tk.Label(panel, text='mobot-0 chesseko', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        tk.Button(panel, text='New game as White', command=lambda: self.new_game(WHITE)).pack(fill='x', pady=(10, 2))
        tk.Button(panel, text='New game as Black', command=lambda: self.new_game(BLACK)).pack(fill='x', pady=2)
        tk.Button(panel, text='Undo move', command=self.undo).pack(fill='x', pady=2)
        tk.Button(panel, text='Flip board', command=self.flip).pack(fill='x', pady=2)

        self.think_time = tk.DoubleVar(value=2.0)
        tk.Scale(panel, from_=0.5, to=10, resolution=0.5, orient='horizontal',
                 label='Engine time per move (s)', variable=self.think_time).pack(fill='x', pady=(10, 0))

        self.status = tk.Label(panel, text='', anchor='w', justify='left', wraplength=220,
                               font=('Segoe UI', 10, 'bold'))
        self.status.pack(fill='x', pady=(10, 0))
        self.eval_label = tk.Label(panel, text='', anchor='w', font=('Segoe UI', 10))
        self.eval_label.pack(fill='x')

        tk.Label(panel, text='Moves', anchor='w').pack(fill='x', pady=(10, 0))
        self.move_list = tk.Text(panel, width=28, height=16, state='disabled',
                                 font=('Consolas', 10), wrap='word')
        self.move_list.pack(fill='both', expand=True)

        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.new_game(WHITE)
        root.after(50, self.poll)

    # ----------------------------------------------------------- game flow

    def new_game(self, human):
        self.abort_search()
        self.board = Board()
        self.human = human
        self.flipped = human == BLACK
        self.selected = None
        self.last_move = None
        self.sans = []
        self.result = None
        self.eval_label.config(text='')
        self.refresh()
        self.after_move()

    def play(self, move):
        self.sans.append(self.board.san(move))
        self.board.make(move)
        self.last_move = move
        self.selected = None
        self.refresh()
        self.after_move()

    def after_move(self):
        self.result = self.board.outcome()
        if self.result:
            score, reason = self.result
            self.set_status(f'Game over: {reason} ({score})')
            self.root.after(100, lambda: messagebox.showinfo('Game over', f'{reason.capitalize()}: {score}'))
        elif self.board.side == self.human:
            self.set_status('Check! Your move.' if self.board.in_check() else 'Your move.')
        else:
            self.start_engine()

    def start_engine(self):
        self.thinking = True
        self.set_status('Engine is thinking...')
        board = self.board.copy()
        seconds = self.think_time.get()
        game_id = self.game_id
        searcher = self.searcher
        searcher.stop_event.clear()

        def work():
            move, score = searcher.think(board, soft_time=seconds * 0.5, hard_time=seconds)
            self.results.put((game_id, move, score, searcher.depth_reached))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def poll(self):
        """Pick up engine results on the Tk thread (Tk isn't thread-safe)."""
        try:
            while True:
                game_id, move, score, depth = self.results.get_nowait()
                if game_id != self.game_id or move is None:
                    continue  # stale result from an aborted search
                self.thinking = False
                white_score = score if self.board.side == WHITE else -score
                self.eval_label.config(text=f'Eval: {format_eval(white_score)} (depth {depth})')
                self.play(move)
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def abort_search(self):
        self.game_id += 1
        if self.worker is not None:
            self.searcher.stop()
            self.worker.join()
            self.worker = None
        self.thinking = False

    def undo(self):
        self.abort_search()
        # Take back to the human's previous turn.
        plies = 2 if self.board.side == self.human else 1
        if len(self.board.stack) < plies:
            self.after_move()
            return
        for _ in range(plies):
            self.board.unmake()
            self.sans.pop()
        self.last_move = self.board.stack[-1][0] if self.board.stack else None
        self.selected = None
        self.refresh()
        self.after_move()

    def flip(self):
        self.flipped = not self.flipped
        self.refresh()

    def on_close(self):
        self.abort_search()
        self.root.destroy()

    # --------------------------------------------------------------- input

    def on_click(self, event):
        if self.thinking or self.result or self.board.side != self.human:
            return
        s = self.square_at(event.x, event.y)
        if s is None:
            return
        if self.selected is not None:
            options = [m for m in self.board.legal_moves()
                       if move_from(m) == self.selected and move_to(m) == s]
            if options:
                move = options[0] if len(options) == 1 else self.ask_promotion(options)
                if move is not None:
                    self.play(move)
                return
        p = self.board.sq[s]
        self.selected = s if p and p >> 3 == self.human and s != self.selected else None
        self.refresh()

    def ask_promotion(self, options):
        chosen = []
        win = tk.Toplevel(self.root)
        win.title('Promote to')
        win.transient(self.root)
        win.resizable(False, False)
        for ptype in (QUEEN, ROOK, BISHOP, KNIGHT):
            move = next(o for o in options if move_promo(o) == ptype)

            def choose(move=move):
                chosen.append(move)
                win.destroy()

            tk.Button(win, text=SOLID[ptype], font=(self.piece_font[0], 32), width=2,
                      command=choose).pack(side='left', padx=4, pady=4)
        win.grab_set()
        self.root.wait_window(win)
        return chosen[0] if chosen else None

    # ------------------------------------------------------------- drawing

    def square_at(self, x, y):
        col, row = x // SQ, y // SQ
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        return square(7 - col, row) if self.flipped else square(col, 7 - row)

    def corner(self, file, rank):
        if self.flipped:
            return (7 - file) * SQ, rank * SQ
        return file * SQ, (7 - rank) * SQ

    def refresh(self):
        self.draw_board()
        self.draw_moves()

    def draw_board(self):
        c = self.canvas
        c.delete('all')
        board = self.board
        targets = set()
        if self.selected is not None:
            targets = {move_to(m) for m in board.legal_moves() if move_from(m) == self.selected}
        last = {move_from(self.last_move), move_to(self.last_move)} if self.last_move else set()
        check = board.kings[board.side] if board.in_check() else None

        for rank in range(8):
            for file in range(8):
                s = square(file, rank)
                x, y = self.corner(file, rank)
                light = (file + rank) % 2 == 1
                color = (LAST_LIGHT if light else LAST_DARK) if s in last else (LIGHT if light else DARK)
                if s == self.selected:
                    color = SELECTED
                if s == check:
                    color = CHECK
                c.create_rectangle(x, y, x + SQ, y + SQ, fill=color, outline='')

                coord_color = DARK if light else LIGHT
                if y + SQ == 8 * SQ:
                    c.create_text(x + SQ - 4, y + SQ - 3, text='abcdefgh'[file], anchor='se',
                                  fill=coord_color, font=self.coord_font)
                if x == 0:
                    c.create_text(x + 3, y + 3, text=str(rank + 1), anchor='nw',
                                  fill=coord_color, font=self.coord_font)

                p = board.sq[s]
                cx, cy = x + SQ / 2, y + SQ / 2 + 2
                if p:
                    ptype = p & 7
                    if p >> 3 == WHITE:
                        # White fill under a black outline glyph gives a crisp white piece.
                        c.create_text(cx, cy, text=SOLID[ptype], fill='white', font=self.piece_font)
                        c.create_text(cx, cy, text=OUTLINE[ptype], fill='black', font=self.piece_font)
                    else:
                        c.create_text(cx, cy, text=SOLID[ptype], fill='black', font=self.piece_font)
                if s in targets:
                    if p:
                        c.create_oval(x + 3, y + 3, x + SQ - 3, y + SQ - 3, outline=TARGET, width=4)
                    else:
                        r = SQ * 0.15
                        c.create_oval(cx - r, cy - 2 - r, cx + r, cy - 2 + r, fill=TARGET, outline='')

    def draw_moves(self):
        lines = []
        for i in range(0, len(self.sans), 2):
            pair = self.sans[i:i + 2]
            lines.append(f'{i // 2 + 1:>3}. {pair[0]:<8}{pair[1] if len(pair) > 1 else ""}')
        self.move_list.config(state='normal')
        self.move_list.delete('1.0', 'end')
        self.move_list.insert('end', '\n'.join(lines))
        self.move_list.see('end')
        self.move_list.config(state='disabled')

    def set_status(self, text):
        self.status.config(text=text)



if __name__ == '__main__':
    root = tk.Tk()
    ChessApp(root)
    root.mainloop()

"""UCI (Universal Chess Interface) front end.

Run `python uci.py` and register it as an engine in any UCI chess GUI
(Arena, Cute Chess, En Croissant, BanksiaGUI, ...) or in lichess-bot.
"""

import sys
import threading
import time

from board import WHITE, Board, move_to_uci
from search import MATE, MATE_BOUND, MAX_PLY, Searcher

NAME = 'mobot-0 chesseko'
MOVE_OVERHEAD = 0.05  # seconds kept in reserve for GUI/communication lag

_print_lock = threading.Lock()


def send(line):
    with _print_lock:
        sys.stdout.write(line + '\n')
        sys.stdout.flush()


def format_score(score):
    if abs(score) >= MATE_BOUND:
        moves = (MATE - abs(score) + 1) // 2
        return f'mate {moves if score > 0 else -moves}'
    return f'cp {score}'


def allocate_time(side, params):
    """Turn UCI 'go' parameters into (soft, hard) limits in seconds."""
    if 'movetime' in params:
        hard = max(0.01, params['movetime'] / 1000 - MOVE_OVERHEAD)
        return hard * 0.6, hard
    clock, inc_key = ('wtime', 'winc') if side == WHITE else ('btime', 'binc')
    if clock not in params:
        return None, None
    left = params[clock] / 1000
    inc = params.get(inc_key, 0) / 1000
    budget = left / max(params.get('movestogo', 30), 1) + 0.75 * inc
    hard = max(0.01, min(3 * budget, left / 2 - MOVE_OVERHEAD))
    soft = min(0.6 * budget, hard)
    return soft, hard


class UciEngine:
    def __init__(self):
        self.board = Board()
        self.searcher = Searcher()
        self.thread = None

    def run(self):
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            tokens = line.split()
            if not tokens:
                continue
            cmd, args = tokens[0], tokens[1:]
            if cmd == 'quit':
                break
            try:
                self.handle(cmd, args)
            except Exception as exc:  # never let a bad command kill the engine
                send(f'info string error: {exc}')
        self.stop_search()

    def handle(self, cmd, args):
        if cmd == 'uci':
            send(f'id name {NAME}')
            send('id author mobot-0 chesseko authors')
            send('option name Hash type spin default 64 min 1 max 1024')
            send('uciok')
        elif cmd == 'isready':
            send('readyok')
        elif cmd == 'setoption':
            self.set_option(args)
        elif cmd == 'ucinewgame':
            self.stop_search()
            self.searcher.clear()
            self.board = Board()
        elif cmd == 'position':
            self.stop_search()
            self.set_position(args)
        elif cmd == 'go':
            self.go(args)
        elif cmd == 'stop':
            self.stop_search()
        elif cmd == 'd':  # non-standard: show the board
            send(str(self.board))
            send(f'Fen: {self.board.fen()}')
        else:
            send(f'info string unknown command: {cmd}')

    def set_option(self, args):
        # setoption name <name> value <value>
        if 'name' not in args or 'value' not in args:
            return
        name = ' '.join(args[args.index('name') + 1:args.index('value')]).lower()
        value = ' '.join(args[args.index('value') + 1:])
        if name == 'hash':
            self.searcher.set_hash_size(max(1, min(1024, int(value))))
            self.searcher.clear()

    def set_position(self, args):
        # position startpos [moves ...] | position fen <fen> [moves ...]
        if 'moves' in args:
            split = args.index('moves')
            setup, moves = args[:split], args[split + 1:]
        else:
            setup, moves = args, []
        if setup and setup[0] == 'fen':
            board = Board(' '.join(setup[1:]))
        else:
            board = Board()
        for text in moves:
            m = board.parse_move(text)
            if m is None:
                send(f'info string illegal move ignored: {text}')
                break
            board.make(m)
        self.board = board

    def go(self, args):
        self.stop_search()
        params = {}
        i = 0
        while i < len(args):
            key = args[i]
            if key in ('wtime', 'btime', 'winc', 'binc', 'movestogo', 'movetime',
                       'depth', 'nodes', 'mate', 'perft') and i + 1 < len(args):
                params[key] = int(args[i + 1])
                i += 2
            else:
                params[key] = True  # infinite, ponder
                i += 1

        if 'perft' in params:
            start = time.perf_counter()
            nodes = self.board.perft(params['perft'])
            send(f'info string perft {params["perft"]}: {nodes} nodes '
                 f'in {time.perf_counter() - start:.2f}s')
            return

        board = self.board.copy()
        soft, hard = allocate_time(board.side, params)
        infinite = 'infinite' in params or 'ponder' in params
        depth = params.get('depth', MAX_PLY)
        searcher = self.searcher
        searcher.stop_event.clear()

        def work():
            move, _ = searcher.think(board, max_depth=depth, soft_time=soft, hard_time=hard,
                                     max_nodes=params.get('nodes'), on_info=self.info)
            if infinite:
                searcher.stop_event.wait()  # UCI: no bestmove until told to stop
            send(f'bestmove {move_to_uci(move) if move else "0000"}')

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def info(self, depth, score, nodes, seconds, pv):
        nps = int(nodes / seconds) if seconds > 0 else 0
        send(f'info depth {depth} score {format_score(score)} nodes {nodes} nps {nps} '
             f'time {int(seconds * 1000)} pv {" ".join(map(move_to_uci, pv))}')

    def stop_search(self):
        if self.thread is not None:
            self.searcher.stop()
            self.thread.join()
            self.thread = None


if __name__ == '__main__':
    UciEngine().run()

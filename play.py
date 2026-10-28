"""Play against the engine in the terminal.

    python play.py                    # you play White, engine thinks 3s per move
    python play.py --color black --time 5
    python play.py --unicode          # use chess symbols instead of letters
"""

import argparse

from board import BLACK, START_FEN, WHITE, Board, move_to_uci, square
from search import MATE, MATE_BOUND, Searcher

UNICODE_PIECES = {
    1: '♙', 2: '♘', 3: '♗', 4: '♖', 5: '♕', 6: '♔',
    9: '♟', 10: '♞', 11: '♝', 12: '♜', 13: '♛', 14: '♚',
}
ASCII_PIECES = '.PNBRQK..pnbrqk'

HELP = """Enter moves in SAN (e4, Nf3, exd5, O-O, e8=Q) or UCI (e2e4, e7e8q) form.
Commands:
  moves   list your legal moves
  hint    ask the engine for a suggestion
  undo    take back your last move
  flip    turn the board around
  fen     print the position as FEN
  help    show this message
  quit    leave the game"""


def render(board, flipped, use_unicode, last_move):
    marked = {last_move & 127, (last_move >> 7) & 127} if last_move else set()
    ranks = range(8) if flipped else range(7, -1, -1)
    files = range(7, -1, -1) if flipped else range(8)
    lines = []
    for rank in ranks:
        cells = []
        for file in files:
            s = square(file, rank)
            p = board.sq[s]
            ch = (UNICODE_PIECES.get(p, '·') if use_unicode else ASCII_PIECES[p])
            cells.append(f'({ch})' if s in marked else f' {ch} ')
        lines.append(f' {rank + 1} ' + ''.join(cells))
    lines.append('    ' + ''.join(f' {"abcdefgh"[f]} ' for f in files))
    return '\n'.join(lines)


def format_eval(score):
    """Format a side-to-move score for humans, e.g. +1.25 or M3."""
    if abs(score) >= MATE_BOUND:
        moves = (MATE - abs(score) + 1) // 2
        return f'{"" if score > 0 else "-"}M{moves}'
    return f'{score / 100:+.2f}'


def main():
    parser = argparse.ArgumentParser(description='Play chess against the engine.')
    parser.add_argument('--color', choices=('white', 'black'), default='white',
                        help='the colour you play (default: white)')
    parser.add_argument('--time', type=float, default=3.0,
                        help='engine thinking time per move in seconds (default: 3)')
    parser.add_argument('--depth', type=int, default=64, help='maximum search depth')
    parser.add_argument('--fen', default=START_FEN, help='start from this position')
    parser.add_argument('--unicode', action='store_true', help='draw pieces with chess symbols')
    args = parser.parse_args()

    board = Board(args.fen)
    human = WHITE if args.color == 'white' else BLACK
    flipped = human != WHITE
    searcher = Searcher()
    last_move = None
    print(HELP)

    while True:
        print()
        print(render(board, flipped, args.unicode, last_move))
        result = board.outcome()
        if result:
            print(f'\nGame over: {result[1]} ({result[0]})')
            break
        if board.in_check():
            print('Check!')

        if board.side != human:
            print('Engine is thinking...')
            move, score = searcher.think(board, max_depth=args.depth,
                                         soft_time=args.time * 0.5, hard_time=args.time)
            white_score = score if board.side == WHITE else -score
            print(f'Engine plays {board.san(move)}   '
                  f'(eval {format_eval(white_score)} for White, depth {searcher.depth_reached})')
            board.make(move)
            last_move = move
            continue

        try:
            text = input('Your move: ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        command = text.lower()
        if not text:
            continue
        if command in ('quit', 'exit', 'q'):
            break
        if command == 'help':
            print(HELP)
        elif command == 'moves':
            print(' '.join(sorted(board.san(m) for m in board.legal_moves())))
        elif command == 'fen':
            print(board.fen())
        elif command == 'flip':
            flipped = not flipped
        elif command == 'hint':
            move, score = searcher.think(board, soft_time=0.5, hard_time=1.0)
            print(f'Hint: {board.san(move)} ({move_to_uci(move)})')
        elif command == 'undo':
            if len(board.stack) >= 2:
                board.unmake()
                board.unmake()
                last_move = board.stack[-1][0] if board.stack else None
            else:
                print('Nothing to undo.')
        else:
            move = board.parse_move(text)
            if move is None:
                print(f'"{text}" is not a legal move here. Type "moves" to see them.')
                continue
            print(f'You play {board.san(move)}')
            board.make(move)
            last_move = move


if __name__ == '__main__':
    main()

# mobot-0 chesseko — a chess engine in pure Python

A complete chess engine written from scratch with only the Python standard
library (Python 3.8+; developed on 3.14). It knows every rule of chess and
searches with alpha-beta and the standard pruning techniques. You can play it
in a window, in the terminal, or plug it into any UCI chess program.

## Playing

```
python gui.py                      # point-and-click board (recommended)
python play.py                     # play in the terminal as White, 3 s per engine move
python play.py --color black --time 5 --unicode
```

In the terminal, type moves in SAN (`e4`, `Nf3`, `exd5`, `O-O`, `e8=Q`) or UCI
form (`e2e4`). Other commands are `moves`, `hint`, `undo`, `flip`, `fen` and `quit`.

## Using it as a UCI engine / bot

`uci.py` speaks the Universal Chess Interface. To add it to a GUI such as
Arena, Cute Chess, En Croissant or BanksiaGUI, set the engine command to:

```
python path/to/mobot-0-chesseko/uci.py
```

Some GUIs only accept an `.exe` or `.bat` file. For those, make a `mobot-0-chesseko.bat`
containing `@python "%~dp0uci.py"` and choose that instead. The same command
also works with [lichess-bot](https://github.com/lichess-bot-devs/lichess-bot)
to run it as a bot account on lichess.org.

It supports `go` with `wtime/btime/winc/binc/movestogo`, `movetime`, `depth`,
`nodes` and `infinite`, plus `stop`, the `Hash` option, and two extras:
`go perft N` and `d` (print the board).

## Files

| File          | What it does |
|---------------|--------------|
| `board.py`    | 10x12 mailbox board, legal move generation, make/unmake, Zobrist hashing, FEN, SAN, draw rules |
| `evaluate.py` | Tapered PeSTO material + piece-square evaluation, updated incrementally; endgame "mop-up" term |
| `search.py`   | Iterative deepening, PVS alpha-beta, aspiration windows, quiescence search, transposition table, null-move / futility / late-move pruning, LMR, killers + history ordering, check extensions |
| `uci.py`      | UCI protocol front end (search runs in a thread so `stop` works) |
| `play.py`     | Terminal game |
| `gui.py`      | Tkinter GUI |
| `tests.py`    | Perft move-generation checks, state/notation/draw-rule tests, tactical tests |

## Testing

```
python -m unittest tests -v
```

Perft results match the published counts for the six standard test
positions, which exercise castling, en passant, promotions and pins.

## Strength and speed

Python is slow for this kind of work. The engine searches roughly 15–30k
nodes per second, which is about depth 7–9 in a few seconds in the
middlegame. It finds short tactics and mates, and converts basic endgames
like K+R vs K. It won't trouble strong engines, though.
Ideas for making it stronger:

- run it under [PyPy](https://pypy.org) for a large speed-up with no code changes
- add pawn-structure, mobility and king-safety terms to `evaluate.py`
- add static exchange evaluation (SEE) to prune losing captures
- add an opening book

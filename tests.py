"""Tests for move generation, board state, notation and search.

    python -m unittest tests -v
"""

import random
import unittest

from board import START_FEN, Board, move_to_uci
from search import MATE, Searcher

# Standard perft positions with known node counts (chessprogramming.org/Perft_Results).
PERFT_CASES = [
    ('start', START_FEN, [20, 400, 8902, 197281]),
    ('kiwipete', 'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1',
     [48, 2039, 97862]),
    ('position 3', '8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1', [14, 191, 2812, 43238]),
    ('position 4', 'r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1',
     [6, 264, 9467]),
    ('position 5', 'rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8', [44, 1486, 62379]),
    ('position 6', 'r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10',
     [46, 2079, 89890]),
]


class PerftTests(unittest.TestCase):
    def test_perft(self):
        for name, fen, counts in PERFT_CASES:
            board = Board(fen)
            for depth, expected in enumerate(counts, 1):
                with self.subTest(position=name, depth=depth):
                    self.assertEqual(board.perft(depth), expected)


class BoardStateTests(unittest.TestCase):
    def test_incremental_state_matches_recomputed(self):
        rng = random.Random(7)
        for _ in range(30):
            board = Board()
            fens = []
            for _ in range(120):
                moves = board.legal_moves()
                if not moves:
                    break
                fens.append(board.fen())
                board.make(rng.choice(moves))
                self.assertEqual((board.hash, board.mg, board.eg, board.npm),
                                 board._compute_state())
            while board.stack:
                board.unmake()
                self.assertEqual(board.fen(), fens.pop())

    def test_fen_round_trip(self):
        for _, fen, _ in PERFT_CASES:
            self.assertEqual(Board(fen).fen(), fen)

    def test_invalid_fen_rejected(self):
        for fen in ('', 'not a fen', '8/8/8/8/8/8/8/8 w - - 0 1', 'rnbqkbnr/pppppppp/8/8 w - - 0 1'):
            with self.assertRaises(ValueError):
                Board(fen)


class NotationTests(unittest.TestCase):
    def play(self, board, *moves):
        for text in moves:
            m = board.parse_move(text)
            self.assertIsNotNone(m, text)
            board.make(m)

    def test_parse_san_and_uci(self):
        board = Board()
        self.play(board, 'e4', 'e7e5', 'Nf3', 'Nc6', 'Bc4', 'Bc5', 'O-O')
        self.assertEqual(board.fen(),
                         'r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 b kq - 5 4')

    def san_set(self, fen):
        board = Board(fen)
        return {board.san(m) for m in board.legal_moves()}

    def test_san_disambiguation(self):
        names = {n.rstrip('+#') for n in self.san_set('3k4/8/8/R7/8/8/4K3/R6R w - - 0 1')}
        self.assertTrue({'Rad1', 'Rhd1', 'R1a3', 'R5a3', 'Rah5', 'Rhh5'} <= names, names)

    def test_san_promotion_check_and_castling(self):
        names = self.san_set('6k1/P7/8/8/8/8/8/R3K2R w KQ - 0 1')
        self.assertTrue({'a8=Q+', 'a8=N', 'O-O', 'O-O-O'} <= names, names)
        self.assertIn('Qxf7#', self.san_set(
            'r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4'))

    def test_illegal_move_rejected(self):
        board = Board()
        self.assertIsNone(board.parse_move('e5'))
        self.assertIsNone(board.parse_move('e1e2'))


class OutcomeTests(unittest.TestCase):
    def test_checkmate(self):
        board = Board()
        for text in ('f3', 'e5', 'g4', 'Qh4'):
            board.make(board.parse_move(text))
        self.assertEqual(board.outcome(), ('0-1', 'checkmate'))

    def test_stalemate(self):
        self.assertEqual(Board('7k/5Q2/6K1/8/8/8/8/8 b - - 0 1').outcome(),
                         ('1/2-1/2', 'stalemate'))

    def test_threefold_repetition(self):
        board = Board()
        for text in ('Nf3', 'Nf6', 'Ng1', 'Ng8') * 2:
            board.make(board.parse_move(text))
        self.assertEqual(board.outcome(), ('1/2-1/2', 'threefold repetition'))

    def test_insufficient_material(self):
        self.assertEqual(Board('8/8/4k3/8/8/2KN4/8/8 w - - 0 1').outcome(),
                         ('1/2-1/2', 'insufficient material'))
        self.assertIsNone(Board('8/8/4k3/8/8/2KR4/8/8 w - - 0 1').outcome())


class SearchTests(unittest.TestCase):
    def best(self, fen, **limits):
        return Searcher().think(Board(fen), **limits)

    def test_mate_in_one(self):
        move, score = self.best(
            'r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4', max_depth=3)
        self.assertEqual(move_to_uci(move), 'h5f7')
        self.assertEqual(score, MATE - 1)

    def test_mate_in_two(self):
        _, score = self.best('7k/8/8/8/8/8/R7/1R4K1 w - - 0 1', max_depth=5)
        self.assertEqual(score, MATE - 3)

    def test_wins_hanging_queen(self):
        move, _ = self.best('rnb1kbnr/pppp1ppp/8/4p1q1/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 1',
                            max_depth=4)
        self.assertEqual(move_to_uci(move), 'c1g5')

    def test_finds_mate_with_rook_ending(self):
        # K+R vs K: the engine must make progress, not shuffle.
        board = Board('8/8/8/4k3/8/8/8/R3K3 w - - 0 1')
        searcher = Searcher()
        for _ in range(60):
            if board.outcome():
                break
            move, _ = searcher.think(board, max_depth=6, hard_time=2)
            board.make(move)
        self.assertEqual(board.outcome(), ('1-0', 'checkmate'))

    def test_abort_leaves_board_untouched(self):
        board = Board('r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1')
        before = board.fen()
        move, _ = Searcher().think(board, hard_time=0.05)
        self.assertIn(move, board.legal_moves())
        self.assertEqual(board.fen(), before)
        self.assertEqual(board.stack, [])

    def test_no_legal_moves(self):
        move, score = self.best('7k/5Q2/6K1/8/8/8/8/8 b - - 0 1')
        self.assertIsNone(move)
        self.assertEqual(score, 0)


if __name__ == '__main__':
    unittest.main()

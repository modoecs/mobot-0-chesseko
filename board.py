"""Board representation, move generation and make/unmake.

Squares live in a 10x12 "mailbox": the real 8x8 board sits inside a border of
OFFBOARD sentinels, so knights and sliding pieces never need bounds checks.
a1 is index 21, h1 is 28, a8 is 91 and h8 is 98.

Moves are plain ints so they are cheap to store, compare and hash:
    bits  0-6   from square
    bits  7-13  to square
    bits 14-16  promotion piece type (0 if none)
    bits 17-18  flag (NORMAL, DOUBLE_PUSH, EN_PASSANT, CASTLING)
"""

import random

from evaluate import EG_TABLE, MG_TABLE, PIECE_PHASE

WHITE, BLACK = 0, 1

EMPTY = 0
PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING = 1, 2, 3, 4, 5, 6
# A piece code is its type, plus 8 if it is black.
WP, WN, WB, WR, WQ, WK = 1, 2, 3, 4, 5, 6
BP, BN, BB, BR, BQ, BK = 9, 10, 11, 12, 13, 14
# OFFBOARD >> 3 == 8 matches neither colour, so "p >> 3 == them" is
# automatically false on the border.
OFFBOARD = 64

NORMAL, DOUBLE_PUSH, EN_PASSANT, CASTLING = 0, 1, 2, 3

START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

PIECE_CHARS = '.PNBRQK..pnbrqk'  # indexed by piece code
CHAR_TO_PIECE = {c: i for i, c in enumerate(PIECE_CHARS) if c != '.'}

KNIGHT_DIRS = (-21, -19, -12, -8, 8, 12, 19, 21)
BISHOP_DIRS = (-11, -9, 9, 11)
ROOK_DIRS = (-10, -1, 1, 10)
KING_DIRS = BISHOP_DIRS + ROOK_DIRS
SLIDER_DIRS = {BISHOP: BISHOP_DIRS, ROOK: ROOK_DIRS, QUEEN: KING_DIRS}


def square(file, rank):
    return 21 + file + 10 * rank


def file_of(s):
    return s % 10 - 1


def rank_of(s):
    return s // 10 - 2


def square_name(s):
    return 'abcdefgh'[file_of(s)] + str(rank_of(s) + 1)


def parse_square(name):
    if len(name) != 2 or name[0] not in 'abcdefgh' or name[1] not in '12345678':
        raise ValueError(f'bad square: {name!r}')
    return square(ord(name[0]) - ord('a'), int(name[1]) - 1)


SQUARES = tuple(square(f, r) for r in range(8) for f in range(8))


def encode_move(fr, to, promo=0, flag=NORMAL):
    return fr | to << 7 | promo << 14 | flag << 17


def move_from(m):
    return m & 127


def move_to(m):
    return (m >> 7) & 127


def move_promo(m):
    return (m >> 14) & 7


def move_flag(m):
    return m >> 17


def move_to_uci(m):
    text = square_name(m & 127) + square_name((m >> 7) & 127)
    promo = (m >> 14) & 7
    if promo:
        text += PIECE_CHARS[promo | 8]  # lowercase letter
    return text


# Zobrist keys. A fixed seed keeps hashes identical between runs.
_rng = random.Random(0x5EED)
Z_PIECE = [[_rng.getrandbits(64) for _ in range(120)] for _ in range(15)]
Z_SIDE = _rng.getrandbits(64)
Z_CASTLE = [_rng.getrandbits(64) for _ in range(16)]
Z_EP = [_rng.getrandbits(64) for _ in range(120)]

# Castling rights bits: 1 = White O-O, 2 = White O-O-O, 4 = Black O-O, 8 = Black O-O-O.
# Moving from or to one of these squares clears the matching rights.
CASTLE_MASK = [15] * 120
CASTLE_MASK[25] = 15 & ~3   # e1
CASTLE_MASK[28] = 15 & ~1   # h1
CASTLE_MASK[21] = 15 & ~2   # a1
CASTLE_MASK[95] = 15 & ~12  # e8
CASTLE_MASK[98] = 15 & ~4   # h8
CASTLE_MASK[91] = 15 & ~8   # a8
# King destination -> (rook from, rook to)
CASTLE_ROOK = {27: (28, 26), 23: (21, 24), 97: (98, 96), 93: (91, 94)}


class Board:
    __slots__ = ('sq', 'side', 'castling', 'ep', 'halfmove', 'fullmove',
                 'kings', 'hash', 'mg', 'eg', 'npm', 'stack', 'history')

    def __init__(self, fen=START_FEN):
        self.set_fen(fen)

    # ------------------------------------------------------------------ FEN

    def set_fen(self, fen):
        parts = fen.split()
        if len(parts) < 4:
            raise ValueError(f'invalid FEN: {fen!r}')
        board = [OFFBOARD] * 120
        for s in SQUARES:
            board[s] = EMPTY
        rows = parts[0].split('/')
        if len(rows) != 8:
            raise ValueError(f'invalid FEN: {fen!r}')
        kings = [0, 0]
        for i, row in enumerate(rows):
            rank, file = 7 - i, 0
            for ch in row:
                if ch.isdigit():
                    file += int(ch)
                elif ch in CHAR_TO_PIECE and file < 8:
                    p = CHAR_TO_PIECE[ch]
                    board[square(file, rank)] = p
                    if p & 7 == KING:
                        if kings[p >> 3]:
                            raise ValueError(f'invalid FEN (two kings): {fen!r}')
                        kings[p >> 3] = square(file, rank)
                    file += 1
                else:
                    raise ValueError(f'invalid FEN: {fen!r}')
            if file != 8:
                raise ValueError(f'invalid FEN: {fen!r}')
        if not kings[0] or not kings[1]:
            raise ValueError(f'invalid FEN (missing king): {fen!r}')
        if parts[1] not in ('w', 'b'):
            raise ValueError(f'invalid FEN: {fen!r}')

        self.sq = board
        self.kings = kings
        self.side = WHITE if parts[1] == 'w' else BLACK
        self.castling = 0
        for ch in parts[2]:
            if ch != '-':
                if ch not in 'KQkq':
                    raise ValueError(f'invalid FEN: {fen!r}')
                self.castling |= {'K': 1, 'Q': 2, 'k': 4, 'q': 8}[ch]
        # Drop rights that the piece placement contradicts.
        for bit, king_sq, king, rook_sq, rook in ((1, 25, WK, 28, WR), (2, 25, WK, 21, WR),
                                                  (4, 95, BK, 98, BR), (8, 95, BK, 91, BR)):
            if board[king_sq] != king or board[rook_sq] != rook:
                self.castling &= ~bit
        self.ep = 0
        if parts[3] != '-':
            ep = parse_square(parts[3])
            # Only remember the en passant square if a capture is really possible,
            # so identical positions always hash identically.
            behind, pawn = (ep - 10, WP) if self.side == WHITE else (ep + 10, BP)
            if board[behind - 1] == pawn or board[behind + 1] == pawn:
                self.ep = ep
        self.halfmove = int(parts[4]) if len(parts) > 4 else 0
        self.fullmove = int(parts[5]) if len(parts) > 5 else 1
        self.stack = []
        self.hash, self.mg, self.eg, self.npm = self._compute_state()
        self.history = [self.hash]

    def fen(self):
        rows = []
        for rank in range(7, -1, -1):
            row, empty = '', 0
            for file in range(8):
                p = self.sq[square(file, rank)]
                if p == EMPTY:
                    empty += 1
                else:
                    if empty:
                        row += str(empty)
                        empty = 0
                    row += PIECE_CHARS[p]
            if empty:
                row += str(empty)
            rows.append(row)
        castling = ''.join(c for bit, c in ((1, 'K'), (2, 'Q'), (4, 'k'), (8, 'q'))
                           if self.castling & bit) or '-'
        ep = square_name(self.ep) if self.ep else '-'
        side = 'w' if self.side == WHITE else 'b'
        return f"{'/'.join(rows)} {side} {castling} {ep} {self.halfmove} {self.fullmove}"

    def _compute_state(self):
        """Compute hash, eval sums and non-pawn material from scratch."""
        h, mg, eg, npm = 0, 0, 0, [0, 0]
        for s in SQUARES:
            p = self.sq[s]
            if p:
                h ^= Z_PIECE[p][s]
                mg += MG_TABLE[p][s]
                eg += EG_TABLE[p][s]
                npm[p >> 3] += PIECE_PHASE[p]
        if self.side == BLACK:
            h ^= Z_SIDE
        h ^= Z_CASTLE[self.castling]
        if self.ep:
            h ^= Z_EP[self.ep]
        return h, mg, eg, npm

    def copy(self):
        other = Board.__new__(Board)
        other.sq = self.sq[:]
        other.side = self.side
        other.castling = self.castling
        other.ep = self.ep
        other.halfmove = self.halfmove
        other.fullmove = self.fullmove
        other.kings = self.kings[:]
        other.hash = self.hash
        other.mg = self.mg
        other.eg = self.eg
        other.npm = self.npm[:]
        other.stack = self.stack[:]
        other.history = self.history[:]
        return other

    def __str__(self):
        rows = []
        for rank in range(7, -1, -1):
            pieces = ' '.join(PIECE_CHARS[self.sq[square(f, rank)]] for f in range(8))
            rows.append(f'{rank + 1}  {pieces}')
        rows.append('   a b c d e f g h')
        return '\n'.join(rows)

    # ------------------------------------------------------- move generation

    def attacked(self, s, by):
        """Is square s attacked by any piece of colour `by`?"""
        b = self.sq
        if by == WHITE:
            if b[s - 9] == WP or b[s - 11] == WP:
                return True
            knight, bishop, rook, queen, king = WN, WB, WR, WQ, WK
        else:
            if b[s + 9] == BP or b[s + 11] == BP:
                return True
            knight, bishop, rook, queen, king = BN, BB, BR, BQ, BK
        for d in KNIGHT_DIRS:
            if b[s + d] == knight:
                return True
        for d in BISHOP_DIRS:
            t = s + d
            p = b[t]
            while p == EMPTY:
                t += d
                p = b[t]
            if p == bishop or p == queen:
                return True
        for d in ROOK_DIRS:
            t = s + d
            p = b[t]
            while p == EMPTY:
                t += d
                p = b[t]
            if p == rook or p == queen:
                return True
        for d in KING_DIRS:
            if b[s + d] == king:
                return True
        return False

    def in_check(self):
        return self.attacked(self.kings[self.side], self.side ^ 1)

    def gen_moves(self, captures_only=False):
        """Pseudo-legal moves (may leave the king in check).

        With captures_only, returns captures plus queen promotions, which is
        what quiescence search wants.
        """
        b = self.sq
        us = self.side
        them = us ^ 1
        ep = self.ep
        moves = []
        add = moves.append
        if us == WHITE:
            up, start_lo, start_hi, promo_lo, promo_hi = 10, 31, 38, 91, 98
        else:
            up, start_lo, start_hi, promo_lo, promo_hi = -10, 81, 88, 21, 28
        promos = (QUEEN,) if captures_only else (QUEEN, ROOK, BISHOP, KNIGHT)

        for s in SQUARES:
            p = b[s]
            if not p or p >> 3 != us:
                continue
            ptype = p & 7
            if ptype == PAWN:
                to = s + up
                if b[to] == EMPTY:
                    if promo_lo <= to <= promo_hi:
                        for pr in promos:
                            add(s | to << 7 | pr << 14)
                    elif not captures_only:
                        add(s | to << 7)
                        if start_lo <= s <= start_hi and b[to + up] == EMPTY:
                            add(s | (to + up) << 7 | DOUBLE_PUSH << 17)
                for to in (s + up - 1, s + up + 1):
                    q = b[to]
                    if q and q >> 3 == them:
                        if promo_lo <= to <= promo_hi:
                            for pr in promos:
                                add(s | to << 7 | pr << 14)
                        else:
                            add(s | to << 7)
                    elif to == ep:
                        add(s | to << 7 | EN_PASSANT << 17)
            elif ptype == KNIGHT or ptype == KING:
                for d in (KNIGHT_DIRS if ptype == KNIGHT else KING_DIRS):
                    to = s + d
                    q = b[to]
                    if q == EMPTY:
                        if not captures_only:
                            add(s | to << 7)
                    elif q >> 3 == them:
                        add(s | to << 7)
            else:
                for d in SLIDER_DIRS[ptype]:
                    to = s + d
                    q = b[to]
                    while q == EMPTY:
                        if not captures_only:
                            add(s | to << 7)
                        to += d
                        q = b[to]
                    if q >> 3 == them:
                        add(s | to << 7)

        if not captures_only and self.castling:
            c = self.castling
            if us == WHITE:
                if (c & 1 and b[26] == EMPTY and b[27] == EMPTY and b[28] == WR
                        and not self.attacked(25, BLACK) and not self.attacked(26, BLACK)
                        and not self.attacked(27, BLACK)):
                    add(25 | 27 << 7 | CASTLING << 17)
                if (c & 2 and b[24] == EMPTY and b[23] == EMPTY and b[22] == EMPTY
                        and b[21] == WR and not self.attacked(25, BLACK)
                        and not self.attacked(24, BLACK) and not self.attacked(23, BLACK)):
                    add(25 | 23 << 7 | CASTLING << 17)
            else:
                if (c & 4 and b[96] == EMPTY and b[97] == EMPTY and b[98] == BR
                        and not self.attacked(95, WHITE) and not self.attacked(96, WHITE)
                        and not self.attacked(97, WHITE)):
                    add(95 | 97 << 7 | CASTLING << 17)
                if (c & 8 and b[94] == EMPTY and b[93] == EMPTY and b[92] == EMPTY
                        and b[91] == BR and not self.attacked(95, WHITE)
                        and not self.attacked(94, WHITE) and not self.attacked(93, WHITE)):
                    add(95 | 93 << 7 | CASTLING << 17)
        return moves

    def legal_moves(self):
        us = self.side
        legal = []
        for m in self.gen_moves():
            self.make(m)
            if not self.attacked(self.kings[us], us ^ 1):
                legal.append(m)
            self.unmake()
        return legal

    # ---------------------------------------------------------- make/unmake

    def make(self, m):
        """Play a (pseudo-legal) move, updating hash and eval incrementally."""
        b = self.sq
        fr = m & 127
        to = (m >> 7) & 127
        promo = (m >> 14) & 7
        flag = m >> 17
        us = self.side
        piece = b[fr]
        cap = b[to]
        npm = self.npm
        self.stack.append((m, cap, self.castling, self.ep, self.halfmove,
                           self.hash, self.mg, self.eg, npm[0], npm[1]))

        h = self.hash ^ Z_SIDE ^ Z_CASTLE[self.castling] ^ Z_PIECE[piece][fr]
        if self.ep:
            h ^= Z_EP[self.ep]
        mg = self.mg - MG_TABLE[piece][fr]
        eg = self.eg - EG_TABLE[piece][fr]
        b[fr] = EMPTY

        if cap:
            h ^= Z_PIECE[cap][to]
            mg -= MG_TABLE[cap][to]
            eg -= EG_TABLE[cap][to]
            npm[us ^ 1] -= PIECE_PHASE[cap]
            self.halfmove = 0
        elif piece & 7 == PAWN:
            self.halfmove = 0
        else:
            self.halfmove += 1

        if promo:
            placed = promo | us << 3
            npm[us] += PIECE_PHASE[placed]
        else:
            placed = piece
        b[to] = placed
        h ^= Z_PIECE[placed][to]
        mg += MG_TABLE[placed][to]
        eg += EG_TABLE[placed][to]

        ep = 0
        if flag == DOUBLE_PUSH:
            enemy_pawn = BP if us == WHITE else WP
            if b[to - 1] == enemy_pawn or b[to + 1] == enemy_pawn:
                ep = (fr + to) >> 1
                h ^= Z_EP[ep]
        elif flag == EN_PASSANT:
            cs = to - 10 if us == WHITE else to + 10
            cp = b[cs]
            b[cs] = EMPTY
            h ^= Z_PIECE[cp][cs]
            mg -= MG_TABLE[cp][cs]
            eg -= EG_TABLE[cp][cs]
        elif flag == CASTLING:
            rf, rt = CASTLE_ROOK[to]
            rook = b[rf]
            b[rf] = EMPTY
            b[rt] = rook
            h ^= Z_PIECE[rook][rf] ^ Z_PIECE[rook][rt]
            mg += MG_TABLE[rook][rt] - MG_TABLE[rook][rf]
            eg += EG_TABLE[rook][rt] - EG_TABLE[rook][rf]

        if piece & 7 == KING:
            self.kings[us] = to
        self.ep = ep
        castling = self.castling & CASTLE_MASK[fr] & CASTLE_MASK[to]
        self.castling = castling
        h ^= Z_CASTLE[castling]
        if us == BLACK:
            self.fullmove += 1
        self.side = us ^ 1
        self.hash = h
        self.mg = mg
        self.eg = eg
        self.history.append(h)

    def unmake(self):
        (m, cap, self.castling, self.ep, self.halfmove,
         self.hash, self.mg, self.eg, npm0, npm1) = self.stack.pop()
        self.npm[0] = npm0
        self.npm[1] = npm1
        self.history.pop()
        us = self.side ^ 1
        self.side = us
        if us == BLACK:
            self.fullmove -= 1
        b = self.sq
        fr = m & 127
        to = (m >> 7) & 127
        piece = b[to]
        if (m >> 14) & 7:
            piece = PAWN | us << 3
        b[fr] = piece
        b[to] = cap
        flag = m >> 17
        if flag == EN_PASSANT:
            b[to - 10 if us == WHITE else to + 10] = PAWN | (us ^ 1) << 3
        elif flag == CASTLING:
            rf, rt = CASTLE_ROOK[to]
            b[rf] = b[rt]
            b[rt] = EMPTY
        if piece & 7 == KING:
            self.kings[us] = fr

    def make_null(self):
        """Pass the turn (used by null-move pruning)."""
        self.stack.append((0, EMPTY, self.castling, self.ep, self.halfmove,
                           self.hash, self.mg, self.eg, self.npm[0], self.npm[1]))
        h = self.hash ^ Z_SIDE
        if self.ep:
            h ^= Z_EP[self.ep]
            self.ep = 0
        self.halfmove = 0  # repetitions can't reach back across a null move
        self.side ^= 1
        self.hash = h
        self.history.append(h)

    def unmake_null(self):
        (_, _, self.castling, self.ep, self.halfmove,
         self.hash, self.mg, self.eg, _, _) = self.stack.pop()
        self.history.pop()
        self.side ^= 1

    # ------------------------------------------------------------ game state

    def is_repetition(self):
        """Has the current position occurred before (since the last irreversible move)?"""
        h = self.hash
        hist = self.history
        lo = len(hist) - 1 - self.halfmove
        i = len(hist) - 3
        while i >= lo and i >= 0:
            if hist[i] == h:
                return True
            i -= 2
        return False

    def repetition_count(self):
        h = self.hash
        hist = self.history
        lo = max(0, len(hist) - 1 - self.halfmove)
        return sum(1 for i in range(len(hist) - 1, lo - 1, -2) if hist[i] == h)

    def insufficient_material(self):
        """K vs K, K+minor vs K, or K+B vs K+B with same-coloured bishops."""
        if self.npm[0] + self.npm[1] > 2:
            return False
        minors = []
        for s in SQUARES:
            ptype = self.sq[s] & 7
            if ptype == PAWN or ptype == ROOK or ptype == QUEEN:
                return False
            if ptype == KNIGHT or ptype == BISHOP:
                minors.append((ptype, (file_of(s) + rank_of(s)) % 2))
        if len(minors) <= 1:
            return True
        return (len(minors) == 2 and minors[0][0] == minors[1][0] == BISHOP
                and minors[0][1] == minors[1][1])

    def outcome(self):
        """Return (result, reason) if the game is over, else None."""
        if not self.legal_moves():
            if self.in_check():
                return ('0-1' if self.side == WHITE else '1-0'), 'checkmate'
            return '1/2-1/2', 'stalemate'
        if self.halfmove >= 100:
            return '1/2-1/2', 'fifty-move rule'
        if self.repetition_count() >= 3:
            return '1/2-1/2', 'threefold repetition'
        if self.insufficient_material():
            return '1/2-1/2', 'insufficient material'
        return None

    def perft(self, depth):
        """Count leaf nodes of the legal move tree (for testing move generation)."""
        if depth == 0:
            return 1
        us = self.side
        total = 0
        for m in self.gen_moves():
            self.make(m)
            if not self.attacked(self.kings[us], us ^ 1):
                total += 1 if depth == 1 else self.perft(depth - 1)
            self.unmake()
        return total

    # -------------------------------------------------------------- notation

    def san(self, m):
        """Standard algebraic notation for a legal move, e.g. Nf3, exd5, O-O, e8=Q+."""
        fr, to, promo, flag = m & 127, (m >> 7) & 127, (m >> 14) & 7, m >> 17
        piece = self.sq[fr]
        ptype = piece & 7
        if flag == CASTLING:
            text = 'O-O' if to > fr else 'O-O-O'
        else:
            capture = self.sq[to] != EMPTY or flag == EN_PASSANT
            if ptype == PAWN:
                text = (square_name(fr)[0] + 'x' if capture else '') + square_name(to)
                if promo:
                    text += '=' + PIECE_CHARS[promo]
            else:
                text = PIECE_CHARS[ptype]
                rivals = [o for o in self.legal_moves()
                          if o != m and (o >> 7) & 127 == to and self.sq[o & 127] == piece]
                if rivals:
                    if not any(file_of(o & 127) == file_of(fr) for o in rivals):
                        text += 'abcdefgh'[file_of(fr)]
                    elif not any(rank_of(o & 127) == rank_of(fr) for o in rivals):
                        text += str(rank_of(fr) + 1)
                    else:
                        text += square_name(fr)
                if capture:
                    text += 'x'
                text += square_name(to)
        self.make(m)
        if self.in_check():
            text += '#' if not self.legal_moves() else '+'
        self.unmake()
        return text

    def parse_move(self, text):
        """Parse a move in UCI (e2e4, e7e8q) or SAN (e4, Nf3, O-O, e8=Q) form.

        Returns the move, or None if it isn't a legal move in this position.
        """
        text = text.strip()
        legal = self.legal_moves()
        for m in legal:
            if move_to_uci(m) == text.lower():
                return m
        wanted = _normalize_san(text)
        for m in legal:
            if _normalize_san(self.san(m)) == wanted:
                return m
        return None


def _normalize_san(text):
    text = text.rstrip('+#!?').replace('=', '')
    if text.lower().replace('0', 'o') in ('o-o', 'o-o-o'):
        return text.upper().replace('0', 'O')
    return text

"""Search: iterative-deepening negamax alpha-beta.

Techniques used, roughly in order of how much strength they add:
  * alpha-beta with principal variation search (PVS) and aspiration windows
  * quiescence search over captures, so the eval is only trusted in quiet positions
  * transposition table (score bound + best move) keyed by Zobrist hash
  * move ordering: TT move, MVV-LVA captures, killer moves, history heuristic
  * null-move pruning, reverse futility pruning, futility pruning,
    late move pruning and late move reductions
  * check extensions and mate-distance pruning
"""

import math
import threading
import time

from board import EMPTY, EN_PASSANT, QUEEN
from evaluate import evaluate

INF = 32000
MATE = 30000
MATE_BOUND = MATE - 1000  # scores beyond this are "mate in N"
MAX_PLY = 64

EXACT, LOWER, UPPER = 0, 1, 2

ORDER_VALUE = (0, 100, 320, 330, 500, 900, 0)  # by piece type, for ordering/delta pruning
FUTILITY_MARGIN = (0, 150, 300, 450)           # by remaining depth
LMP_LIMIT = (0, 6, 10, 16)                     # quiet moves tried before pruning the rest
HISTORY_MAX = 50_000

LMR = [[0] * 64 for _ in range(64)]
for _d in range(1, 64):
    for _i in range(1, 64):
        LMR[_d][_i] = int(0.75 + math.log(_d) * math.log(_i) / 2.25)


class SearchAborted(Exception):
    """Raised inside the search when time, nodes or stop() say to give up."""


class Searcher:
    def __init__(self, hash_mb=64):
        self.tt = {}
        self.tt_max = 0
        self.set_hash_size(hash_mb)
        self.stop_event = threading.Event()
        self.board = None
        self.nodes = 0
        self.deadline = None
        self.max_nodes = None
        self.root_best = 0
        self.depth_reached = 0
        self.pv = [[] for _ in range(MAX_PLY + 2)]
        self.killers = [[0, 0] for _ in range(MAX_PLY + 2)]
        self.history = [[0] * 120 for _ in range(15)]

    def set_hash_size(self, mb):
        # A dict slot plus its entry tuple costs roughly 200 bytes in CPython.
        self.tt_max = max(1 << 12, mb * (1 << 20) // 200)

    def clear(self):
        self.tt.clear()

    def stop(self):
        """Ask a running search (in another thread) to finish as soon as possible.

        The event stays set until the caller clears it before the next search.
        """
        self.stop_event.set()

    def think(self, board, max_depth=MAX_PLY, soft_time=None, hard_time=None,
              max_nodes=None, on_info=None):
        """Search the position and return (best_move, score).

        score is in centipawns from the side to move's point of view, or
        +/-(MATE - plies) for a forced mate. The search stops at max_depth,
        when hard_time seconds or max_nodes are used up, or when stop() is
        called; no new iteration starts after soft_time seconds. Each finished
        iteration calls on_info(depth, score, nodes, seconds, pv). The board is
        left exactly as it was given. best_move is None if there are no legal moves.
        """
        root_moves = board.legal_moves()
        if not root_moves:
            return None, (-MATE if board.in_check() else 0)

        start = time.perf_counter()
        self.board = board
        self.nodes = 0
        self.deadline = start + hard_time if hard_time is not None else None
        self.max_nodes = max_nodes
        self.killers = [[0, 0] for _ in range(MAX_PLY + 2)]
        self.history = [[0] * 120 for _ in range(15)]
        self.root_best = root_moves[0]
        self.depth_reached = 0
        best_move, best_score = root_moves[0], 0
        base = len(board.stack)

        for depth in range(1, min(max_depth, MAX_PLY - 1) + 1):
            try:
                score = self._root_search(depth, best_score)
            except SearchAborted:
                while len(board.stack) > base:
                    if board.stack[-1][0] == 0:
                        board.unmake_null()
                    else:
                        board.unmake()
                # A move that raised alpha in the unfinished iteration has already
                # beaten the previous best move, so it is safe to use.
                best_move = self.root_best
                break
            best_move, best_score = self.root_best, score
            self.depth_reached = depth
            elapsed = time.perf_counter() - start
            if on_info:
                on_info(depth, score, self.nodes, elapsed, list(self.pv[0]))
            if soft_time is not None and elapsed >= soft_time:
                break
            if len(root_moves) == 1 and (soft_time is not None or hard_time is not None):
                break  # forced move: don't waste the clock
            if abs(score) >= MATE_BOUND and depth > MATE - abs(score) + 1:
                break  # mate found and confirmed; deeper search won't change it
        return best_move, best_score

    # ----------------------------------------------------------------- core

    def _poll(self):
        if (self.stop_event.is_set()
                or (self.deadline is not None and time.perf_counter() >= self.deadline)
                or (self.max_nodes is not None and self.nodes >= self.max_nodes)):
            raise SearchAborted

    def _root_search(self, depth, prev_score):
        """Search the root with an aspiration window around the previous score."""
        if depth < 4 or abs(prev_score) >= MATE_BOUND:
            return self._negamax(depth, -INF, INF, 0, True)
        delta = 40
        alpha, beta = prev_score - delta, prev_score + delta
        while True:
            score = self._negamax(depth, alpha, beta, 0, True)
            if score <= alpha:
                alpha = max(score - delta, -INF)
            elif score >= beta:
                beta = min(score + delta, INF)
            else:
                return score
            delta *= 2

    def _order(self, moves, tt_move, ply):
        sq = self.board.sq
        killer0, killer1 = self.killers[ply]
        history = self.history
        keyed = []
        for m in moves:
            if m == tt_move:
                key = 10_000_000
            else:
                to = (m >> 7) & 127
                victim = sq[to]
                promo = (m >> 14) & 7
                if victim:
                    # MVV-LVA: most valuable victim first, then least valuable attacker.
                    key = 1_000_000 + 10 * ORDER_VALUE[victim & 7] - (sq[m & 127] & 7)
                elif m >> 17 == EN_PASSANT:
                    key = 1_000_000 + 10 * ORDER_VALUE[1] - 1
                elif promo:
                    key = 900_000 if promo == QUEEN else -100_000
                elif m == killer0:
                    key = 800_000
                elif m == killer1:
                    key = 790_000
                else:
                    key = history[sq[m & 127]][to]
            keyed.append((key, m))
        keyed.sort(reverse=True)
        return keyed

    def _reward_quiet(self, move, tried, depth, ply):
        """A quiet move caused a beta cutoff: remember it for move ordering."""
        killers = self.killers[ply]
        if killers[0] != move:
            killers[1] = killers[0]
            killers[0] = move
        sq = self.board.sq
        bonus = depth * depth
        row = self.history[sq[move & 127]]
        to = (move >> 7) & 127
        row[to] = min(row[to] + bonus, HISTORY_MAX)
        for q in tried:
            row = self.history[sq[q & 127]]
            to = (q >> 7) & 127
            row[to] = max(row[to] - bonus, -HISTORY_MAX)

    def _negamax(self, depth, alpha, beta, ply, allow_null):
        board = self.board
        self.pv[ply] = []
        self.nodes += 1
        if not self.nodes & 1023:
            self._poll()

        if ply:
            if board.halfmove >= 100 or board.is_repetition():
                return 0
            if board.npm[0] + board.npm[1] <= 2 and board.insufficient_material():
                return 0
            if ply >= MAX_PLY:
                return evaluate(board)
            # Mate distance pruning: nothing here can beat a mate already found.
            if alpha < -MATE + ply:
                alpha = -MATE + ply
            if beta > MATE - ply - 1:
                beta = MATE - ply - 1
            if alpha >= beta:
                return alpha

        us = board.side
        them = us ^ 1
        in_check = board.attacked(board.kings[us], them)
        if in_check:
            depth += 1
        if depth <= 0:
            return self._quiesce(alpha, beta, ply)

        pv_node = beta - alpha > 1
        tt_move = 0
        entry = self.tt.get(board.hash)
        if entry is not None:
            tt_depth, tt_flag, tt_score, tt_move = entry
            if not pv_node and tt_depth >= depth:
                if tt_score > MATE_BOUND:
                    tt_score -= ply
                elif tt_score < -MATE_BOUND:
                    tt_score += ply
                if (tt_flag == EXACT
                        or (tt_flag == LOWER and tt_score >= beta)
                        or (tt_flag == UPPER and tt_score <= alpha)):
                    return tt_score

        static_eval = 0
        if not in_check:
            static_eval = evaluate(board)
            if not pv_node and abs(beta) < MATE_BOUND:
                # Reverse futility: so far above beta that a shallow search won't fall below it.
                if depth <= 6 and static_eval - 90 * depth >= beta:
                    return static_eval
                # Null move: if passing still beats beta, a real move almost surely will.
                if allow_null and depth >= 3 and static_eval >= beta and board.npm[us]:
                    r = 3 + depth // 6
                    board.make_null()
                    score = -self._negamax(depth - 1 - r, -beta, -beta + 1, ply + 1, False)
                    board.unmake_null()
                    if score >= beta:
                        return beta if score >= MATE_BOUND else score
        futile = (not in_check and not pv_node and depth <= 3
                  and static_eval + FUTILITY_MARGIN[depth] <= alpha)

        sq = board.sq
        orig_alpha = alpha
        best = -INF
        best_move = 0
        legal = 0
        quiets = []
        for _, m in self._order(board.gen_moves(), tt_move, ply):
            to = (m >> 7) & 127
            quiet = sq[to] == EMPTY and (m >> 14) & 7 == 0 and m >> 17 != EN_PASSANT
            board.make(m)
            if board.attacked(board.kings[us], them):
                board.unmake()
                continue
            legal += 1
            gives_check = board.attacked(board.kings[them], us)

            if quiet and legal > 1 and not in_check and not gives_check and best > -MATE_BOUND:
                if futile or (not pv_node and depth <= 3 and legal > LMP_LIMIT[depth]):
                    board.unmake()
                    continue

            if legal == 1:
                score = -self._negamax(depth - 1, -beta, -alpha, ply + 1, True)
            else:
                r = 0
                if depth >= 3 and quiet and legal > 3 and not in_check and not gives_check:
                    r = LMR[min(depth, 63)][min(legal, 63)]
                    if pv_node:
                        r -= 1
                    r = max(0, min(r, depth - 2))
                score = -self._negamax(depth - 1 - r, -alpha - 1, -alpha, ply + 1, True)
                if r and score > alpha:
                    score = -self._negamax(depth - 1, -alpha - 1, -alpha, ply + 1, True)
                if alpha < score < beta:
                    score = -self._negamax(depth - 1, -beta, -alpha, ply + 1, True)
            board.unmake()

            if score > best:
                best = score
                best_move = m
                if score > alpha:
                    alpha = score
                    self.pv[ply] = [m] + self.pv[ply + 1]
                    if ply == 0:
                        self.root_best = m
                    if score >= beta:
                        if quiet:
                            self._reward_quiet(m, quiets, depth, ply)
                        break
            if quiet:
                quiets.append(m)

        if not legal:
            return -MATE + ply if in_check else 0

        if best >= beta:
            flag = LOWER
        elif best > orig_alpha:
            flag = EXACT
        else:
            flag = UPPER
            best_move = tt_move  # no move proved best; keep the old hint
        stored = best
        if stored > MATE_BOUND:
            stored += ply
        elif stored < -MATE_BOUND:
            stored -= ply
        if len(self.tt) >= self.tt_max:
            self.tt.clear()
        self.tt[board.hash] = (depth, flag, stored, best_move)
        return best

    def _quiesce(self, alpha, beta, ply):
        """Search captures only, until the position is quiet enough to evaluate."""
        self.nodes += 1
        if not self.nodes & 1023:
            self._poll()
        board = self.board
        stand_pat = evaluate(board)
        if ply >= MAX_PLY or stand_pat >= beta:
            return stand_pat
        if stand_pat > alpha:
            alpha = stand_pat

        sq = board.sq
        keyed = []
        for m in board.gen_moves(True):
            victim = sq[(m >> 7) & 127] & 7
            if not victim and m >> 17 == EN_PASSANT:
                victim = 1
            gain = ORDER_VALUE[victim] + (800 if (m >> 14) & 7 else 0)
            if stand_pat + gain + 200 <= alpha:
                continue  # delta pruning: even winning this can't raise alpha
            keyed.append((10 * gain - (sq[m & 127] & 7), m))
        keyed.sort(reverse=True)

        us = board.side
        them = us ^ 1
        best = stand_pat
        for _, m in keyed:
            board.make(m)
            if board.attacked(board.kings[us], them):
                board.unmake()
                continue
            score = -self._quiesce(-beta, -alpha, ply + 1)
            board.unmake()
            if score > best:
                best = score
                if score > alpha:
                    if score >= beta:
                        return score
                    alpha = score
        return best

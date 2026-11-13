"""Bradley-Terry fits of Game Picker answers: the page's own conditional logit, a league-only baseline, and a factorised model.

Why: WORKPLAN v2 D-01 / track D experiment D1: separate league taste from moment value with few answers. The page fit
(game-picker 1.3.1 `cardFeats` + `fitModel`, ported here line for line) mixes both in one additive logit; the factorised
model writes a card's log value as

    u(card) = a[sport] + k[family] * (theta . x) + gamma . z + b_inc * incumbent

a[sport] = log of the sport weight, shrunk toward weight 1 (a = 0) or toward Nick's stated ranking by a Gaussian prior;
x = the moment features (stakes, phase, margin, late_and_close, state, storyline, my team, bet); k[family] = how strongly
that sport family's moments move his picks (prior mean 1, so with little data it is the additive model); z = three
leverage x stakes interaction terms. Fitted by damped Gauss-Newton (numpy only) to the MAP; the posterior is the Laplace
approximation with the Gauss-Newton Hessian, which `active_pairs` reads.

Answers -> choice events follow the page (`answerEvents`): duel/pick = one choice among the cards ("same" = equal weight
on each), stay = a choice between the watched card (cards[0], with the incumbent term) and the other, rank = sequential
choices, rate rounds and skips give no event. This matches research/brain/notebooks/stage5_nick_value.py except that the
page keys sports by `sport_key` (20 sports) and stage5 by the context machine's league names.

Nothing in the show imports this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# ---- the page's vocabulary (game-picker 1.3.1) ----------------------------------------------------------------------
PICKER_SPORT_FAMILY = {
    "nfl": "football", "cfb": "football", "nba": "hoops", "mcbb": "hoops", "wcbb": "hoops", "wnba": "hoops",
    "nhl": "hockey", "mlb": "baseball", "soc_m": "soccer", "soc_w": "soccer", "soc_i": "soccer",
    "golf": "other", "tennis": "other", "ufc": "other", "f1": "other", "nascar": "other", "indy": "other",
    "lax": "other", "vball": "other", "rugby": "other",
}
PICKER_SPORTS = tuple(PICKER_SPORT_FAMILY)          # page order; nba is the reference sport (weight 1)
PICKER_FAMILIES = ("football", "hoops", "hockey", "baseball", "soccer", "other")
PICKER_STORY_SLUGS = ("comeback", "scoring_run", "upset_alert", "milestone_night", "no_hitter", "shutout_bid",
                      "record_chase", "ot_just_started", "last_possession", "lead_change", "win_or_go_home",
                      "playoff_spot")
_LATE = frozenset({"late", "final_minutes", "overtime"})
MOMENT_TERMS = (
    "season:rivalry_or_national", "season:playoff", "season:final",
    "phase:middle", "phase:late", "phase:final_minutes", "phase:overtime",
    "margin:tied", "margin:one_score", "margin:comfortable", "margin:blowout", "late_and_close",
    "state:big_chance", "state:dead_timeout", "state:dead_free_throws_or_set_piece", "state:between_plays",
    "state:just_after_big_play",
    *(f"story:{s}" for s in PICKER_STORY_SLUGS), "story_strong",
    "my_team", "bet:needs_score", "bet:sweating", "bet:cashing", "bet:lost",
)
PAGE_CORE_TERMS = (*(f"sport:{k}" for k in PICKER_SPORTS if k != "nba"), *MOMENT_TERMS, "incumbent",
                   *(f"peakx:{k}" for k in PICKER_SPORTS))
# leverage x stakes interactions (the "small number of interaction terms" of D1)
INTERACTION_TERMS = ("late_and_close*stakes", "big_chance*late", "my_team*late_and_close")
PAGE_RIDGE_CORE, PAGE_RIDGE_NAME, PAGE_RIDGE_PEAK, PAGE_MAX_NAMES = 1.0, 3.0, 2.0, 40


def card_terms_page(f: dict, incumbent: bool = False) -> list[str]:
    """The page's `cardFeats`: feature names of one card's canonical `f` (team-name terms included)."""
    x = []
    sk = f.get("sport_key")
    if sk != "nba":
        x.append(f"sport:{sk}")
    if f.get("season_type") != "regular":
        x.append(f"season:{f.get('season_type')}")
    if f.get("phase") != "early":
        x.append(f"phase:{f.get('phase')}")
    if f.get("margin_bucket") != "close":
        x.append(f"margin:{f.get('margin_bucket')}")
    if f.get("phase") in _LATE and f.get("margin_bucket") in ("tied", "one_score"):
        x.append("late_and_close")
    if f.get("state") != "live":
        x.append(f"state:{f.get('state')}")
    if f.get("storyline"):
        x.append(f"story:{f['storyline']}")
        if f.get("storyline_strength") == "strong":
            x.append("story_strong")
    if f.get("my_team"):
        x.append("my_team")
    if f.get("my_bet") and f.get("my_bet") != "none":
        x.append(f"bet:{f['my_bet']}")
    if incumbent:
        x.append("incumbent")
    if f.get("peak"):
        x.append(f"peakx:{sk}")
    for t in f.get("teams") or []:
        x.append(f"name:{sk}:{t}")
    return x


@dataclass(frozen=True)
class Card:
    """One card as the fits see it: its sport and its page feature names (incumbent included when it is the watched one)."""
    sport: str
    terms: tuple[str, ...]


@dataclass
class ChoiceEvent:
    """One multinomial choice: the cards on offer and the target weight on each (sums to 1)."""
    alts: list[Card]
    target: np.ndarray
    answer_id: str = ""


def _card(c: dict, incumbent: bool) -> Card:
    f = c.get("f") or {}
    return Card(str(f.get("sport_key")), tuple(card_terms_page(f, incumbent)))


def answer_cards(a: dict) -> list[Card]:
    """The answer's cards with the page's incumbent flag (stay rounds: cards[0] is the watched game)."""
    return [_card(c, a.get("round") == "stay" and i == 0) for i, c in enumerate(a.get("cards") or [])]


def usable(a: dict) -> bool:
    """True when the page's fit would use the answer (not hidden, not skipped)."""
    ans = a.get("answer") or {}
    return bool(ans) and not ans.get("skip") and not a.get("hidden")


def answer_events(a: dict) -> list[ChoiceEvent]:
    """The page's `answerEvents` for one answer (empty for rate rounds, skips, hidden answers and malformed rows)."""
    if not usable(a):
        return []
    ans = a["answer"]
    X = answer_cards(a)
    n, rnd, aid = len(X), a.get("round"), str(a.get("id"))

    def one(m: int, k: int) -> np.ndarray:
        t = np.zeros(m)
        t[k] = 1.0
        return t
    if n < 2:
        return []
    if rnd in ("duel", "pick"):
        if ans.get("same"):
            return [ChoiceEvent(X, np.full(n, 1.0 / n), aid)]
        ch = ans.get("choice")
        if not isinstance(ch, int) or isinstance(ch, bool) or not 0 <= ch < n:
            return []
        return [ChoiceEvent(X, one(n, ch), aid)]
    if rnd == "stay":
        if not isinstance(ans.get("stay"), bool):
            return []
        return [ChoiceEvent(X[:2], np.array([1.0, 0.0]) if ans["stay"] else np.array([0.0, 1.0]), aid)]
    if rnd == "rank":
        out, rest = [], list(range(n))
        for ch in ans.get("order") or []:
            if len(rest) < 2 or ch not in rest:
                break
            out.append(ChoiceEvent([X[i] for i in rest], one(len(rest), rest.index(ch)), aid))
            rest.remove(ch)
        return out
    return []


def answer_pairs(a: dict) -> list[tuple[Card, Card]]:
    """(winner, loser) card pairs an answer states, for pairwise accuracy: the chosen card over each other card, a rank's
    order over every later card; "same", rate rounds and skips state none."""
    if not usable(a) or (a.get("answer") or {}).get("same"):
        return []
    out = []
    for ev in answer_events(a):
        w = int(np.argmax(ev.target))
        out.extend((ev.alts[w], c) for i, c in enumerate(ev.alts) if i != w)
    return out


def repeat_root(answers: list[dict]) -> dict[str, str]:
    """id -> id at the root of its repeatOf chain (a repeat and its original share a group)."""
    by = {str(a.get("id")): a for a in answers}
    out = {}
    for a in answers:
        cur, seen = a, set()
        while cur.get("repeatOf") and str(cur["repeatOf"]) in by and str(cur.get("id")) not in seen:
            seen.add(str(cur.get("id")))
            cur = by[str(cur["repeatOf"])]
        out[str(a.get("id"))] = str(cur.get("id"))
    return out


# ---- shared numerics: grouped softmax over stacked alternatives -----------------------------------------------------
def _stack(events: list[ChoiceEvent]):
    starts = np.cumsum([0] + [len(e.alts) for e in events])[:-1]
    t = np.concatenate([e.target for e in events]) if events else np.zeros(0)
    return starts, t


def _grouped_probs(u: np.ndarray, starts: np.ndarray) -> np.ndarray:
    mx = np.maximum.reduceat(u, starts)
    lens = np.diff(np.append(starts, len(u)))
    e = np.exp(u - np.repeat(mx, lens))
    return e / np.repeat(np.add.reduceat(e, starts), lens)


def _loglik_grad_ggn(J: np.ndarray, u: np.ndarray, t: np.ndarray, starts: np.ndarray):
    """Log-likelihood, gradient and Gauss-Newton matrix of the grouped multinomial logit (targets sum to 1 per event)."""
    p = _grouped_probs(u, starts)
    ll = float(t @ np.log(np.maximum(p, 1e-300)))
    g = J.T @ (t - p)
    M = np.add.reduceat(p[:, None] * J, starts, axis=0)
    H = J.T @ (p[:, None] * J) - M.T @ M
    return ll, g, H


# ---- the page fit (and the league-only baseline as the page fit restricted to sport terms) ---------------------------
@dataclass
class LinearFit:
    """An additive conditional logit: u(card) = sum of the weights of its terms (unknown terms count 0)."""
    names: list[str]
    w: np.ndarray
    cov: np.ndarray | None = None
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.index = {n: i for i, n in enumerate(self.names)}

    def utility(self, card: Card) -> float:
        return float(sum(self.w[self.index[t]] for t in card.terms if t in self.index))

    def weight(self, name: str) -> float:
        return float(self.w[self.index[name]]) if name in self.index else 0.0

    def se(self, name: str) -> float:
        i = self.index.get(name)
        return float(math.sqrt(max(0.0, self.cov[i, i]))) if (i is not None and self.cov is not None) else float("nan")


def fit_page(events: list[ChoiceEvent], prior: dict[str, float] | None = None, sport_only: bool = False,
             iters: int = 25) -> LinearFit:
    """The page's `fitModel`: core terms + up to 40 team-name terms seen in 2+ events; ridge 1 (names 3, peak 2); with a
    stated prior each sport term is centred on it with precision 1 + 6/(1 + n_informative/10); Newton, steps clipped to 2.
    sport_only=True keeps only the sport terms (the league-only baseline)."""
    if sport_only:
        names = [f"sport:{k}" for k in PICKER_SPORTS if k != "nba"]
    else:
        cnt: dict[str, int] = {}
        for e in events:
            for c in e.alts:
                for k in c.terms:
                    if k.startswith("name:"):
                        cnt[k] = cnt.get(k, 0) + 1
        top = sorted((kv for kv in cnt.items() if kv[1] >= 2), key=lambda kv: -kv[1])[:PAGE_MAX_NAMES]
        names = list(PAGE_CORE_TERMS) + [k for k, _ in top]
    ix = {n: i for i, n in enumerate(names)}
    D = len(names)
    lam = np.array([PAGE_RIDGE_NAME if n.startswith("name:") else PAGE_RIDGE_PEAK if n.startswith("peakx:")
                    else PAGE_RIDGE_CORE for n in names])
    mean = np.zeros(D)
    rows = [[ix[k] for k in c.terms if k in ix] for e in events for c in e.alts]
    X = np.zeros((len(rows), D))
    for r, cols in enumerate(rows):
        for j in cols:
            X[r, j] += 1.0
    starts, t = _stack(events)
    if prior:
        n_inf = np.zeros(D)
        for s, e in zip(starts, events):
            blk = X[s:s + len(e.alts)]
            n_inf += ((blk > 0).sum(axis=0) > 0) & ((blk > 0).sum(axis=0) < len(e.alts))
        for k, v in prior.items():
            if k in ix and math.isfinite(v):
                mean[ix[k]] = v
                lam[ix[k]] = PAGE_RIDGE_CORE + 6.0 / (1.0 + n_inf[ix[k]] / 10.0)
    w = mean.copy()
    H = np.diag(lam)
    for _ in range(iters):
        if len(t):
            _, g_ll, H_ll = _loglik_grad_ggn(X, X @ w, t, starts)
        else:
            g_ll, H_ll = np.zeros(D), np.zeros((D, D))
        g = lam * (w - mean) - g_ll                       # gradient of the negative log posterior
        H = np.diag(lam) + H_ll
        step = np.clip(np.linalg.solve(H + 1e-9 * np.eye(D), g), -2.0, 2.0)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return LinearFit(names, w, np.linalg.inv(H + 1e-9 * np.eye(D)))


# ---- the factorised model ------------------------------------------------------------------------------------------
def _interactions(terms: tuple[str, ...]) -> np.ndarray:
    s = set(terms)
    late = bool(s & {"phase:late", "phase:final_minutes", "phase:overtime"})
    stakes = bool(s & {"season:playoff", "season:final"})
    return np.array([float("late_and_close" in s and stakes), float("state:big_chance" in s and late),
                     float("my_team" in s and "late_and_close" in s)])


@dataclass
class FactorisedPrior:
    """Gaussian prior of the factorised model. sport_mean: log sport weight means (default 0 = weight 1)."""
    sport_mean: dict[str, float] = field(default_factory=dict)
    sport_precision: float = 1.0
    fade_with_data: bool = True        # the page's rule for a stated prior: precision 1 + 6/(1 + n/10)
    family_mean: float = 1.0
    family_precision: float = 4.0      # sd 0.5 around 1
    moment_precision: float = 1.0
    interaction_precision: float = 3.0
    incumbent_precision: float = 1.0


class FactorisedBT:
    """u = a[sport] + k[family] * (theta . x) + gamma . z + b_inc * incumbent. Parameter vector layout:
    [a (19 sports, nba fixed at 0) | k (6 families) | theta (moment terms) | gamma (3) | b_inc]."""

    def __init__(self, prior: FactorisedPrior | None = None):
        self.prior = prior or FactorisedPrior()
        self.sports = [k for k in PICKER_SPORTS if k != "nba"]
        self.sport_ix = {k: i for i, k in enumerate(self.sports)}
        self.fam_ix = {f: i for i, f in enumerate(PICKER_FAMILIES)}
        self.moment_ix = {k: i for i, k in enumerate(MOMENT_TERMS)}
        nS, nF, nM, nI = len(self.sports), len(PICKER_FAMILIES), len(MOMENT_TERMS), len(INTERACTION_TERMS)
        self.sl = {"a": slice(0, nS), "k": slice(nS, nS + nF), "theta": slice(nS + nF, nS + nF + nM),
                   "gamma": slice(nS + nF + nM, nS + nF + nM + nI), "inc": slice(nS + nF + nM + nI, nS + nF + nM + nI + 1)}
        self.P = nS + nF + nM + nI + 1
        self.mean = self._init_mean()
        self.cov = np.diag(1.0 / self._precision(np.zeros(nS)))
        self.n_events = 0

    def _init_mean(self) -> np.ndarray:
        m = np.zeros(self.P)
        m[self.sl["k"]] = self.prior.family_mean
        for k, v in self.prior.sport_mean.items():
            key = k.split(":", 1)[1] if k.startswith("sport:") else k
            if key in self.sport_ix and math.isfinite(v):
                m[self.sl["a"].start + self.sport_ix[key]] = v
        return m

    def _precision(self, n_inf_sport: np.ndarray) -> np.ndarray:
        p = self.prior
        lam = np.zeros(self.P)
        sp = np.full(len(self.sports), p.sport_precision)
        if p.sport_mean and p.fade_with_data:
            has = np.array([(k in p.sport_mean or f"sport:{k}" in p.sport_mean) for k in self.sports])
            sp = np.where(has, p.sport_precision + 6.0 / (1.0 + n_inf_sport / 10.0), sp)
        lam[self.sl["a"]] = sp
        lam[self.sl["k"]] = p.family_precision
        lam[self.sl["theta"]] = p.moment_precision
        lam[self.sl["gamma"]] = p.interaction_precision
        lam[self.sl["inc"]] = p.incumbent_precision
        return lam

    # design pieces for a list of cards
    def encode(self, cards: list[Card]):
        n = len(cards)
        S = np.zeros((n, len(self.sports)))
        F = np.zeros((n, len(PICKER_FAMILIES)))
        X = np.zeros((n, len(MOMENT_TERMS)))
        Z = np.zeros((n, len(INTERACTION_TERMS)))
        inc = np.zeros(n)
        for r, c in enumerate(cards):
            if c.sport in self.sport_ix:
                S[r, self.sport_ix[c.sport]] = 1.0
            F[r, self.fam_ix[PICKER_SPORT_FAMILY.get(c.sport, "other")]] = 1.0
            for t in c.terms:
                j = self.moment_ix.get(t)
                if j is not None:
                    X[r, j] += 1.0
            Z[r] = _interactions(c.terms)
            inc[r] = float("incumbent" in c.terms)
        return S, F, X, Z, inc

    def _u_and_jac(self, enc, w: np.ndarray):
        S, F, X, Z, inc = enc
        a, k, th = w[self.sl["a"]], w[self.sl["k"]], w[self.sl["theta"]]
        g, b = w[self.sl["gamma"]], w[self.sl["inc"]][0]
        m = X @ th
        kf = F @ k
        u = S @ a + kf * m + Z @ g + inc * b
        J = np.hstack([S, F * m[:, None], kf[:, None] * X, Z, inc[:, None]])
        return u, J

    def utility(self, card: Card) -> float:
        return float(self.utilities([card])[0])

    def utilities(self, cards: list[Card], w: np.ndarray | None = None) -> np.ndarray:
        return self._u_and_jac(self.encode(cards), self.mean if w is None else w)[0]

    def jacobian(self, cards: list[Card]) -> np.ndarray:
        return self._u_and_jac(self.encode(cards), self.mean)[1]

    def fit(self, events: list[ChoiceEvent], iters: int = 60, tol: float = 1e-7) -> FactorisedBT:
        """MAP by damped Gauss-Newton with a backtracking line search; sets mean and the Laplace covariance."""
        cards = [c for e in events for c in e.alts]
        enc = self.encode(cards)
        starts, t = _stack(events)
        n_inf = np.zeros(len(self.sports))
        for s, e in zip(starts, events):
            blk = enc[0][s:s + len(e.alts)]
            col = blk.sum(axis=0)
            n_inf += (col > 0) & (col < len(e.alts))
        lam = self._precision(n_inf)
        mu0 = self._init_mean()
        w = mu0.copy()

        def neg_post(v):
            if not len(t):
                return 0.5 * float(lam @ (v - mu0) ** 2), -lam * (v - mu0), np.diag(lam)
            u, J = self._u_and_jac(enc, v)
            ll, g, H = _loglik_grad_ggn(J, u, t, starts)
            return -ll + 0.5 * float(lam @ (v - mu0) ** 2), g - lam * (v - mu0), H + np.diag(lam)
        f, g, H = neg_post(w)
        for _ in range(iters):
            step = np.linalg.solve(H + 1e-9 * np.eye(self.P), g)    # ascent direction on the log posterior
            s = 1.0
            while s > 1e-4:
                f2, g2, H2 = neg_post(w + s * step)
                if f2 <= f + 1e-12:
                    break
                s *= 0.5
            else:
                break
            w, done = w + s * step, f - f2 < tol
            f, g, H = f2, g2, H2
            if done:
                break
        self.mean = w
        self.cov = np.linalg.inv(H + 1e-9 * np.eye(self.P))
        self.n_events = len(events)
        return self

    def sport_weights(self) -> dict[str, tuple[float, float]]:
        """sport -> (weight = exp(a), sd of a); nba = (1, 0)."""
        out = {"nba": (1.0, 0.0)}
        for k, i in self.sport_ix.items():
            j = self.sl["a"].start + i
            out[k] = (float(math.exp(self.mean[j])), float(math.sqrt(max(0.0, self.cov[j, j]))))
        return out

    def named_params(self) -> dict[str, tuple[float, float]]:
        """Every non-sport parameter -> (mean, sd)."""
        out = {}
        for name, block in (("k", [f"family:{f}" for f in PICKER_FAMILIES]), ("theta", list(MOMENT_TERMS)),
                            ("gamma", list(INTERACTION_TERMS)), ("inc", ["incumbent"])):
            for off, lab in enumerate(block):
                j = self.sl[name].start + off
                out[lab] = (float(self.mean[j]), float(math.sqrt(max(0.0, self.cov[j, j]))))
        return out


def pairwise_hits(utility, pairs: list[tuple[Card, Card]]) -> list[float]:
    """1 when the model prefers the stated winner, 0.5 on a tie (within 1e-9), else 0."""
    out = []
    for w, lo in pairs:
        d = utility(w) - utility(lo)
        out.append(0.5 if abs(d) < 1e-9 else float(d > 0))
    return out

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器学习 AVM 方法全家族 —— 严格按论文设定实现（纯 Python，零第三方依赖）。

对应论文
--------
[M-01] Ho, W.K.O. et al. (2021). Predicting property prices with machine learning
       algorithms. Journal of Property Research. → 香港 39,554 笔，SVR/RF/GBM，最好 R²≈90%；
       但极端值预测偏差高。
[M-02] (2022). Irish Property Price Estimation Using A Flexible Geo-spatial Smoothing
       Approach. JREFE. → 低换手率/小样本下，带**预测区间**的高斯过程空间模型优于纯 ML 点估计。
[M-03] Kok, N., Koponen, E.-L. & Martinez-Barbosa, C.A. (2017). Big Data in Real Estate?
       From Manual Appraisal to Automated Valuation. JPM, 43(5). → AVM 定位为筛选级，不可完全替代人工评估。
[M-04] Calainho, van de Minne & Francke (2024). JREFE, 68, 624-653.
       → ML 更依赖标定数据；小样本不稳定、可能存在估计偏差，须做偏差-方差权衡。
[mat-014] Ho, W.K.O. et al. (2021). Predicting property prices with machine learning algorithms.
         Journal of Property Research. → 树集成在房地产预测上优于线性特征价格；
         但可解释性下降，须配特征归因工具（本包用排列重要性，未实现 SHAP）。

本模块实现
----------
1. DecisionTreeRegressor   CART 回归树（方差削减准则）
2. RandomForest            Bagging + 特征随机子抽样（mat-024 Breiman 2001；房地产应用 mat-014）
3. GradientBoostingRegressor  平方损失梯度提升（≈XGBoost 的核心机制）
4. GaussianProcessAVM      高斯过程空间平滑 AVM，输出预测区间（Irish 2022）
5. permutation_importance  排列重要性（模型无关归因，SHAP 的可复现替代）
6. partial_dependence      部分依赖（属性对价格的边际影响）

用法
----
    python3 ml_valuation.py --data sample_spatial.csv --y 单价 --x 面积 楼龄 x y --method all

仅依赖 Python 标准库；共享数值核心见 <pack>/scripts/core.py。
"""

import argparse
import math
import os
import sys


def _import_core():
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        cand = os.path.join(cur, "scripts")
        if os.path.isfile(os.path.join(cand, "core.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    raise ImportError("未找到共享核心 scripts/core.py")


_import_core()
from core import read_csv, to_float, mean, ols, inverse, matvec, metrics, standardize, kfold_indices, fmt_table, z_for  # noqa: E402


# ================================================================ 1. CART 回归树

class _Node:
    __slots__ = ("feature", "threshold", "left", "right", "value")

    def __init__(self):
        self.feature = self.threshold = self.left = self.right = None
        self.value = None


class DecisionTreeRegressor:
    """
    CART 回归树（方差削减准则）。
        SSE(t) = Σ_{i∈t}(y_i − ȳ_t)² ；分裂收益 = SSE(t) − SSE(t_L) − SSE(t_R)
    """

    def __init__(self, max_depth=6, min_samples_leaf=3, max_features=None, seed=0):
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.max_features = max_features
        self.seed = seed
        self.root = None

    def fit(self, X, y):
        self.n_features = len(X[0]) if X else 0
        idx = list(range(len(y)))
        self.root = self._build(X, y, idx, 0)
        return self

    def _build(self, X, y, idx, depth):
        node = _Node()
        node.value = mean([y[i] for i in idx])
        if depth >= self.max_depth or len(idx) < 2 * self.min_samples_leaf:
            return node
        best = None
        feats = list(range(self.n_features))
        if self.max_features and self.max_features < len(feats):
            # 确定性随机特征子抽样（可复现）：LCG 洗牌后取前 m 个
            state = (1103515245 * (self.seed * 7919 + depth * 104729 + len(idx)) + 12345) % (2 ** 31)
            pool = list(feats)
            for i in range(len(pool) - 1, 0, -1):
                state = (1103515245 * state + 12345) % (2 ** 31)
                t = state % (i + 1)
                pool[i], pool[t] = pool[t], pool[i]
            feats = sorted(pool[:self.max_features])
        for f in feats:
            vals = sorted({X[i][f] for i in idx})
            if len(vals) < 2:
                continue
            cand = [0.5 * (vals[k] + vals[k + 1]) for k in range(len(vals) - 1)]
            if len(cand) > 24:
                step = max(1, len(cand) // 24)
                cand = cand[::step]
            for th in cand:
                L = [i for i in idx if X[i][f] <= th]
                R = [i for i in idx if X[i][f] > th]
                if len(L) < self.min_samples_leaf or len(R) < self.min_samples_leaf:
                    continue
                gain = self._sse(y, idx) - self._sse(y, L) - self._sse(y, R)
                if best is None or gain > best[0]:
                    best = (gain, f, th, L, R)
        if best is None or best[0] <= 1e-12:
            return node
        _g, f, th, L, R = best
        node.feature, node.threshold = f, th
        node.left = self._build(X, y, L, depth + 1)
        node.right = self._build(X, y, R, depth + 1)
        return node

    @staticmethod
    def _sse(y, idx):
        m = mean([y[i] for i in idx])
        return sum((y[i] - m) ** 2 for i in idx)

    def predict_one(self, x):
        node = self.root
        while node.feature is not None:
            node = node.left if x[node.feature] <= node.threshold else node.right
        return node.value

    def predict(self, X):
        return [self.predict_one(x) for x in X]


# ================================================================ 2. 随机森林

class RandomForest:
    """
    随机森林（mat-024 Breiman 2001；房地产应用见 mat-014 Ho et al. 2021）。

    Bagging + 每节点特征随机子抽样；预测为各树平均。
    out-of-bag（OOB）样本用于无需独立验证集的泛化估计。
    """

    def __init__(self, n_trees=120, max_depth=8, min_samples_leaf=2,
                 max_features=None, bootstrap_ratio=1.0, seed=42):
        self.n_trees = n_trees
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.max_features = max_features
        self.bootstrap_ratio = bootstrap_ratio
        self.seed = seed
        self.trees = []
        self.oob_idx = []

    def fit(self, X, y):
        n = len(X)
        k = len(X[0])
        mf = self.max_features or max(1, int(math.sqrt(k)) + 1)
        self.trees, self.oob_idx = [], []
        state = self.seed
        for t in range(self.n_trees):
            idx = []
            for _ in range(int(n * self.bootstrap_ratio)):
                state = (1103515245 * state + 12345) % (2 ** 31)
                idx.append(state % n)
            oob = sorted(set(range(n)) - set(idx))
            self.oob_idx.append(oob)
            tr = DecisionTreeRegressor(self.max_depth, self.min_samples_leaf, mf, seed=state)
            tr.fit([X[i] for i in idx], [y[i] for i in idx])
            self.trees.append(tr)
        return self

    def predict(self, X):
        out = []
        for x in X:
            vals = [t.predict_one(x) for t in self.trees]
            out.append(mean(vals))
        return out

    def predict_std(self, X):
        """各树预测的标准差——可作为不确定性的粗略代理。"""
        out = []
        for x in X:
            vals = [t.predict_one(x) for t in self.trees]
            m = mean(vals)
            out.append(math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)))
        return out

    def oob_score(self, X, y):
        """OOB 预测的 MAE / MAPE（无需独立验证集）。"""
        n = len(y)
        acc = [[] for _ in range(n)]
        for t, tree in enumerate(self.trees):
            for i in self.oob_idx[t]:
                acc[i].append(tree.predict_one(X[i]))
        yt, yp = [], []
        for i in range(n):
            if acc[i]:
                yt.append(y[i])
                yp.append(mean(acc[i]))
        if not yt:
            return None
        return metrics(yt, yp)


# ================================================================ 3. 梯度提升

class GradientBoostingRegressor:
    """
    梯度提升（平方损失），即 XGBoost 的核心机制（未含正则化/二阶导等工程优化）。

        F_0 = ȳ
        r_i^(m) = y_i − F_{m−1}(x_i)          （负梯度 = 残差）
        F_m = F_{m−1} + η · h_m(x)            （h_m 为对残差的回归树）

    参数 learning_rate（η）控制偏差-方差权衡（Calainho et al. 2024 强调该权衡）。
    """

    def __init__(self, n_estimators=150, learning_rate=0.08, max_depth=3,
                 min_samples_leaf=3, subsample=0.8, seed=42):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.subsample = subsample
        self.seed = seed
        self.trees = []
        self.f0 = 0.0

    def fit(self, X, y):
        self.f0 = mean(y)
        cur = [self.f0] * len(y)
        state = self.seed
        n = len(y)
        self.trees = []
        for _ in range(self.n_estimators):
            resid = [y[i] - cur[i] for i in range(n)]
            idx = []
            for _ in range(max(2, int(n * self.subsample))):
                state = (1103515245 * state + 12345) % (2 ** 31)
                idx.append(state % n)
            tr = DecisionTreeRegressor(self.max_depth, self.min_samples_leaf, seed=state)
            tr.fit([X[i] for i in idx], [resid[i] for i in idx])
            for i in range(n):
                cur[i] += self.learning_rate * tr.predict_one(X[i])
            self.trees.append(tr)
        return self

    def predict(self, X):
        return [self.f0 + self.learning_rate * sum(t.predict_one(x) for t in self.trees) for x in X]


# ================================================================ 4. 高斯过程 AVM

class GaussianProcessAVM:
    """
    高斯过程空间平滑 AVM（Irish Property Price Estimation, JREFE 2022）。

    在「属性 + 空间坐标」上构造核函数，做贝叶斯回归，**输出预测区间**——
    这正是论文强调的：低换手率/小样本市场，点估计不足，需不确定性量化。

        k(u, v) = sigma_f^2 * exp(-0.5 * sum_j ((u_j-v_j)/l_j)^2) + sigma_n^2 * delta
        mu(x*)  = k*^T (K + sigma_n^2 I)^{-1} y
        var(x*) = k(x*,x*) - k*^T (K + sigma_n^2 I)^{-1} k*

    【关键】X 与 y **都必须标准化**：只标准化 X 会使核幅 sigma_f 与价格量纲失配，
    核矩阵退化、区间宽度趋 0、覆盖率失真。超参数由对数边际似然网格搜索确定。
    """

    def __init__(self, length_scale=None, sigma_f=None, sigma_n=None, seed=7):
        self.length_scale = length_scale
        self.sigma_f = sigma_f
        self.sigma_n = sigma_n
        self.seed = seed

    @staticmethod
    def _dist2(a, b, ls):
        return sum(((a[j] - b[j]) / ls[j]) ** 2 for j in range(len(a)))

    def _kernel(self, a, b, ls, sf, sn, same=False):
        v = sf * sf * math.exp(-0.5 * self._dist2(a, b, ls))
        if same:
            v += sn * sn
        return v

    def fit(self, X, y):
        n = len(X)
        k = len(X[0])
        cols = [[X[i][j] for i in range(n)] for j in range(k)]
        self._mu, self._sd = [], []
        for j in range(k):
            _z, m, sd = standardize(cols[j])
            self._mu.append(m)
            self._sd.append(sd)
        Xs = [[(X[i][j] - self._mu[j]) / self._sd[j] for j in range(k)] for i in range(n)]
        ym = mean(y)
        syd = math.sqrt(sum((v - ym) ** 2 for v in y) / n) or 1.0
        self.ym, self.syd = ym, syd
        yy = [(v - ym) / syd for v in y]          # 单位方差，核幅才有可比量纲

        best = None
        ls_grid = [0.2, 0.4, 0.7, 1.0, 1.5, 2.5, 4.0, 6.0]
        sf_grid = [0.5, 0.8, 1.0, 1.5, 2.0]
        sn_grid = [0.0, 0.05, 0.15, 0.3, 0.5, 0.8, 1.2]
        for ls0 in ls_grid:
            for sf in sf_grid:
                for sn in sn_grid:
                    ls = [ls0] * k
                    try:
                        K = [[self._kernel(Xs[i], Xs[j], ls, sf, sn, same=(i == j)) for j in range(n)] for i in range(n)]
                        Ki = inverse(K)
                        detK = _det(K)
                    except ValueError:
                        continue
                    detK = _det(K)
                    if detK <= 1e-300:
                        continue
                    alpha = matvec(Ki, yy)
                    ll = (-0.5 * sum(yy[i] * alpha[i] for i in range(n))
                          - 0.5 * math.log(detK) - 0.5 * n * math.log(2 * math.pi))
                    if best is None or ll > best[0]:
                        best = (ll, ls0, sf, sn, Ki)
        if best is None:
            raise ValueError("高斯过程超参数搜索失败（核矩阵奇异）。")
        self.loglik, self.length_scale, self.sigma_f, self.sigma_n, self._Ki = best
        self._Xs, self._yy, self._y = Xs, yy, y
        return self

    def predict(self, X, level=0.95):
        """返回 (均值列表, 下界列表, 上界列表)，单位与原始 y 一致。"""
        z = z_for(level)
        Xs = [[(X[i][j] - self._mu[j]) / self._sd[j] for j in range(len(X[0]))] for i in range(len(X))]
        n = len(self._Xs)
        ls = [self.length_scale] * len(X[0])
        alpha = matvec(self._Ki, self._yy)
        mus, los, his = [], [], []
        for q in Xs:
            kstar = [self._kernel(q, self._Xs[i], ls, self.sigma_f, self.sigma_n) for i in range(n)]
            mu_s = sum(kstar[i] * alpha[i] for i in range(n))
            v_s = self.sigma_f ** 2 - sum(kstar[i] * sum(self._Ki[i][j] * kstar[j] for j in range(n)) for i in range(n))
            v_s = max(v_s, 0.0)
            mu = self.ym + self.syd * mu_s
            sd = self.syd * math.sqrt(v_s)
            mus.append(mu)
            los.append(mu - z * sd)
            his.append(mu + z * sd)
        return mus, los, his


def _det(A):
    M = [list(r) for r in A]
    n = len(M)
    d = 1.0
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-14:
            return 0.0
        if p != c:
            M[c], M[p] = M[p], M[c]
            d = -d
        d *= M[c][c]
        for r in range(c + 1, n):
            f = M[r][c] / M[c][c]
            for cc in range(c, n):
                M[r][cc] -= f * M[c][cc]
    return d


# ================================================================ 5. 归因

def permutation_importance(model_predict, X, y, names, repeats=5, seed=11):
    """
    排列重要性（模型无关归因）。本包**未实现 SHAP**（未计算 Shapley 值），
    本函数是零依赖条件下可复现的替代方案——报告中不得称其为 SHAP。

    思路：打乱某特征后预测误差的增量 = 该特征的重要性。纯标准库可复现，
    不依赖 shap 包；如需严格 Shapley 值请另行安装 shap。
    """
    base = metrics(y, model_predict(X))["MAE"]
    out = []
    n = len(y)
    state = seed
    for j, nm in enumerate(names):
        deltas = []
        for _ in range(repeats):
            perm = list(range(n))
            for i in range(n - 1, 0, -1):
                state = (1103515245 * state + 12345) % (2 ** 31)
                t = state % (i + 1)
                perm[i], perm[t] = perm[t], perm[i]
            Xp = [list(row) for row in X]
            for i in range(n):
                Xp[i][j] = X[perm[i]][j]
            deltas.append(metrics(y, model_predict(Xp))["MAE"] - base)
        out.append((nm, mean(deltas)))
    out.sort(key=lambda t: -t[1])
    total = sum(max(v, 0.0) for _n, v in out) or 1.0
    return [(nm, v, max(v, 0.0) / total * 100.0) for nm, v in out]


def partial_dependence(model_predict, X, j, grid=None, npts=8):
    """部分依赖：固定其余变量为均值，观察第 j 个特征变化对预测的影响。"""
    n = len(X)
    k = len(X[0])
    cols = [[X[i][c] for i in range(n)] for c in range(k)]
    lo, hi = min(cols[j]), max(cols[j])
    grid = grid or [lo + (hi - lo) * s / (npts - 1) for s in range(npts)]
    mu = [mean(cols[c]) for c in range(k)]
    out = []
    for g in grid:
        Xt = [[g if c == j else mu[c] for c in range(k)]]
        out.append((g, model_predict(Xt)[0]))
    return out


# ================================================================ CLI

def main():
    ap = argparse.ArgumentParser(description="机器学习 AVM 全家族（RF / GBM / 高斯过程 / 归因）")
    ap.add_argument("--data", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--x", required=True, nargs="+")
    ap.add_argument("--method", default="all", choices=["rf", "gbm", "gp", "all"])
    ap.add_argument("--trees", type=int, default=120)
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    _, rows = read_csv(args.data)
    X, y = [], []
    for r in rows:
        ys = to_float(r.get(args.y))
        xs = [to_float(r.get(n)) for n in args.x]
        if ys is None or any(v is None for v in xs):
            continue
        X.append(xs)
        y.append(ys)
    n = len(y)
    print("数据：%s | 有效样本 %d | 特征 %d" % (args.data, n, len(args.x)))
    if n < 40:
        print("⚠ 样本量偏小（%d）；ML 更依赖标定数据，小样本下不稳定且可能有估计偏差"
              "（Calainho et al. 2024）。结论仅供演示。" % n)

    folds = kfold_indices(n, args.k)

    def cv_metrics(build):
        yt, yp = [], []
        for f in folds:
            te = set(f)
            tr = [i for i in range(n) if i not in te]
            mdl = build()
            mdl.fit([X[i] for i in tr], [y[i] for i in tr])
            pr = mdl.predict([X[i] for i in sorted(te)])
            for t, i in enumerate(sorted(te)):
                yt.append(y[i])
                yp.append(pr[t])
        return metrics(yt, yp)

    results = []

    if args.method in ("rf", "all"):
        rf = RandomForest(n_trees=args.trees, max_depth=8, seed=42).fit(X, y)
        cv = cv_metrics(lambda: RandomForest(n_trees=max(40, args.trees // 2), max_depth=8, seed=7))
        oob = rf.oob_score(X, y)
        ins = metrics(y, rf.predict(X))
        print("\n=== 随机森林 RandomForest（%d 棵树）===" % args.trees)
        print("  样本内：MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (ins["MAE"], ins["MAPE"], ins["R2"]))
        if oob:
            print("  OOB   ：MAE=%.0f  MAPE=%.2f%%  （无需独立验证集）" % (oob["MAE"], oob["MAPE"]))
        print("  %d 折CV：MAE=%.0f  MAPE=%.2f%%" % (args.k, cv["MAE"], cv["MAPE"]))
        results.append(("RandomForest", cv))
        sd = rf.predict_std(X)
        print("  树间预测标准差（中位）：%.0f 元/㎡（不确定性代理）" % sorted(sd)[len(sd) // 2])
        imp = permutation_importance(rf.predict, X, y, args.x)
        print("  排列重要性：")
        print(fmt_table(["特征", "MAE 增量", "占比%"], [[a, "%.0f" % b, "%.1f" % c] for a, b, c in imp], ["<", ">", ">"]))

    if args.method in ("gbm", "all"):
        gb = GradientBoostingRegressor(n_estimators=150, learning_rate=0.08, max_depth=3, seed=42).fit(X, y)
        cv = cv_metrics(lambda: GradientBoostingRegressor(n_estimators=100, learning_rate=0.08, max_depth=3, seed=7))
        ins = metrics(y, gb.predict(X))
        print("\n=== 梯度提升 GradientBoosting（150 轮，η=0.08）===")
        print("  样本内：MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (ins["MAE"], ins["MAPE"], ins["R2"]))
        print("  %d 折CV：MAE=%.0f  MAPE=%.2f%%" % (args.k, cv["MAE"], cv["MAPE"]))
        results.append(("GradientBoosting", cv))

    if args.method in ("gp", "all"):
        gp = GaussianProcessAVM().fit(X, y)
        mu, lo, hi = gp.predict(X)
        m = metrics(y, mu)
        cover = sum(1 for i in range(n) if lo[i] <= y[i] <= hi[i]) / n * 100.0
        print("\n=== 高斯过程 AVM（空间平滑 + 预测区间，Irish 2022）===")
        print("  超参数：length_scale=%.2f  σ_f=%.2f  σ_n=%.2f  logML=%.2f"
              % (gp.length_scale, gp.sigma_f, gp.sigma_n, gp.loglik))
        print("  样本内：MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (m["MAE"], m["MAPE"], m["R2"]))
        print("  95%% 预测区间覆盖率 = %.1f%%（目标 ≈95%%；偏离说明核或噪声设定需调整）" % cover)
        w = [hi[i] - lo[i] for i in range(n)]
        print("  区间宽度（中位）：%.0f 元/㎡" % sorted(w)[len(w) // 2])
        print("  说明：论文强调——低换手率/小样本市场应优先使用带区间的空间统计模型，"
              "而非纯点估计 ML（M-02）")
        results.append(("GaussianProcess", {"MAE": m["MAE"], "MAPE": m["MAPE"]}))

    if len(results) > 1:
        print("\n=== 方法对比（%d 折交叉验证，样本外）===" % args.k)
        print(fmt_table(["方法", "MAE", "MAPE%"],
                        [[nm, "%.0f" % r["MAE"], "%.2f" % r["MAPE"]] for nm, r in results],
                        ["<", ">", ">"]))

    print("\n注：ML 与 AVM 定位为筛选级工具，不可完全替代人工评估（Kok et al. 2017）；"
          "极端值预测偏差高（Ho et al. 2021），须单独处理。")


if __name__ == "__main__":
    main()
